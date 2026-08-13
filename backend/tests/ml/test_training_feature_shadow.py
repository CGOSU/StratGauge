import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml.training_datasets import TrainingDatasetPurpose
from stratgauge.ml.training_feature_registry import (
    FeatureModelRegistryError,
    FeatureModelRegistryStore,
)
from stratgauge.ml.training_feature_shadow import (
    FeatureShadowError,
    FeatureShadowInput,
    FeatureShadowScoreStore,
    feature_shadow_input_sha256,
    read_feature_shadow_inputs,
    score_feature_shadow_batch,
)
from tests.ml.test_training_feature_registry import (
    _binary_training_result,
    _multiclass_training_result,
    _write_outputs,
)


class FakeNumpy:
    @staticmethod
    def asarray(matrix: list[list[float]], *, dtype: type[float]) -> list[list[float]]:
        return matrix


class FakeBinaryBooster:
    def predict(self, matrix: list[list[float]]) -> list[float]:
        return [0.7 for _ in matrix]


class FakeMulticlassBooster:
    def predict(self, matrix: list[list[float]]) -> list[list[float]]:
        return [[0.1, 0.2, 0.7] for _ in matrix]


class FakeLightGBM:
    booster_type: type[object] = FakeBinaryBooster

    @classmethod
    def Booster(cls, *, model_str: str) -> object:
        if model_str != "feature-lightgbm-fixture":
            raise ValueError("unexpected runtime artifact")
        return cls.booster_type()


class TrainingFeatureShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeLightGBM.booster_type = FakeBinaryBooster

    def test_shadow_requires_manual_transition_and_keeps_binary_score_non_authoritative(
        self,
    ) -> None:
        training_result = _binary_training_result()

        with TemporaryDirectory() as directory:
            training_dir, _ = _write_outputs(directory, training_result)
            registry = FeatureModelRegistryStore(directory)
            registration = registry.register(
                training_dir,
                registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                registered_by="researcher",
            )
            inputs = (_input(registration.purpose, _snapshot("feature-1", date(2026, 3, 5))),)

            with patch("stratgauge.ml.training_feature_shadow.import_module"):
                with self.assertRaisesRegex(FeatureShadowError, "shadow lifecycle"):
                    score_feature_shadow_batch(
                        inputs,
                        registry=registry,
                        score_store=FeatureShadowScoreStore(directory),
                        registration_id=registration.registration_id,
                        scored_at=datetime(2026, 3, 5, 17, tzinfo=UTC),
                    )

            registration = registry.transition(
                registration.registration_id,
                "shadow",
                changed_at=datetime(2026, 3, 4, tzinfo=UTC),
                reason="人工启用 shadow 验证",
                operator="researcher",
            )
            with patch(
                "stratgauge.ml.training_feature_shadow.import_module",
                side_effect=lambda name: FakeLightGBM if name == "lightgbm" else FakeNumpy,
            ):
                report = score_feature_shadow_batch(
                    inputs,
                    registry=registry,
                    score_store=FeatureShadowScoreStore(directory),
                    registration_id=registration.registration_id,
                    scored_at=datetime(2026, 3, 5, 17, tzinfo=UTC),
                )

            self.assertEqual(report.source_input_sha256, feature_shadow_input_sha256(inputs))
            self.assertEqual(len(report.score_ids), 1)
            self.assertEqual(report.failures, ())
            score = FeatureShadowScoreStore(directory).load_score(report.score_ids[0])
            self.assertEqual(score.purpose, TrainingDatasetPurpose.CANDIDATE_SUCCESS)
            self.assertAlmostEqual(float(score.raw_probabilities["not_success"]), 0.3)
            self.assertAlmostEqual(float(score.raw_probabilities["success"]), 0.7)
            self.assertEqual(score.predicted_label.value, "success")
            self.assertEqual(score.raw_predicted_label.value, "success")
            self.assertFalse(score.calibrated)
            self.assertFalse(score.used_for_decision)

            self.assertEqual(
                registry.list_events(registration.registration_id)[0].to_lifecycle, "shadow"
            )

    def test_feature_shadow_rejects_outcome_labels_and_keeps_bad_feature_as_candidate_failure(
        self,
    ) -> None:
        training_result = _binary_training_result()

        with TemporaryDirectory() as directory:
            training_dir, _ = _write_outputs(directory, training_result)
            registry = FeatureModelRegistryStore(directory)
            registration = registry.register(
                training_dir,
                registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                registered_by="researcher",
            )
            registry.transition(
                registration.registration_id,
                "shadow",
                changed_at=datetime(2026, 3, 4, tzinfo=UTC),
                reason="shadow",
                operator="researcher",
            )
            valid = _input(registration.purpose, _snapshot("feature-valid", date(2026, 3, 5)))
            invalid = _input(
                registration.purpose,
                _snapshot("feature-invalid", date(2026, 3, 5), feature_set_version_id="wrong"),
            )
            with self.assertRaisesRegex(FeatureShadowError, "outcome labels"):
                read_feature_shadow_inputs(
                    (
                        json.dumps(
                            {
                                **valid.model_dump(mode="json"),
                                "target": "success",
                            }
                        )
                        + "\n"
                    ).encode("utf-8")
                )

            with patch(
                "stratgauge.ml.training_feature_shadow.import_module",
                side_effect=lambda name: FakeLightGBM if name == "lightgbm" else FakeNumpy,
            ):
                report = score_feature_shadow_batch(
                    (valid, invalid),
                    registry=registry,
                    score_store=FeatureShadowScoreStore(directory),
                    registration_id=registration.registration_id,
                    scored_at=datetime(2026, 3, 5, 17, tzinfo=UTC),
                )

            self.assertEqual(len(report.score_ids), 1)
            self.assertEqual(len(report.failures), 1)
            self.assertEqual(report.failures[0].candidate_id, "candidate-feature-invalid")
            self.assertIn("feature set", report.failures[0].reason)

    def test_multiclass_shadow_can_use_registered_calibration_only(self) -> None:
        training_result = _multiclass_training_result()

        with TemporaryDirectory() as directory:
            training_dir, calibration_dir = _write_outputs(directory, training_result)
            assert calibration_dir is not None
            registry = FeatureModelRegistryStore(directory)
            registration = registry.register(
                training_dir,
                calibration_output_dir=calibration_dir,
                registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                registered_by="researcher",
            )
            registry.transition(
                registration.registration_id,
                "shadow",
                changed_at=datetime(2026, 3, 4, tzinfo=UTC),
                reason="shadow",
                operator="researcher",
            )
            inputs = (
                _input(
                    registration.purpose,
                    _snapshot("feature-multi", date(2026, 3, 5)),
                ),
            )
            FakeLightGBM.booster_type = FakeMulticlassBooster
            with patch(
                "stratgauge.ml.training_feature_shadow.import_module",
                side_effect=lambda name: FakeLightGBM if name == "lightgbm" else FakeNumpy,
            ):
                report = score_feature_shadow_batch(
                    inputs,
                    registry=registry,
                    score_store=FeatureShadowScoreStore(directory),
                    registration_id=registration.registration_id,
                    scored_at=datetime(2026, 3, 5, 17, tzinfo=UTC),
                    calibration_mode="registered",
                )

            score = FeatureShadowScoreStore(directory).load_score(report.score_ids[0])
            self.assertTrue(score.calibrated)
            self.assertEqual(score.calibration_id, registration.calibration_id)
            self.assertEqual(
                set(score.calibrated_probabilities or {}), {"discard", "watch", "prepare_buy"}
            )
            self.assertAlmostEqual(
                float(sum((score.calibrated_probabilities or {}).values(), Decimal("0"))),
                1.0,
            )

    def test_registry_lifecycle_rejects_retrograde_transition(self) -> None:
        training_result = _binary_training_result()

        with TemporaryDirectory() as directory:
            training_dir, _ = _write_outputs(directory, training_result)
            registry = FeatureModelRegistryStore(directory)
            registration = registry.register(
                training_dir,
                registered_at=datetime(2026, 3, 3, tzinfo=UTC),
                registered_by="researcher",
            )
            registry.transition(
                registration.registration_id,
                "shadow",
                changed_at=datetime(2026, 3, 4, tzinfo=UTC),
                reason="shadow",
                operator="researcher",
            )
            with self.assertRaisesRegex(FeatureModelRegistryError, "must advance"):
                registry.transition(
                    registration.registration_id,
                    "retired",
                    changed_at=datetime(2026, 3, 4, tzinfo=UTC),
                    reason="retire",
                    operator="researcher",
                )


def _input(
    purpose: TrainingDatasetPurpose,
    snapshot: FeatureSnapshot,
) -> FeatureShadowInput:
    return FeatureShadowInput(
        candidate_id=f"candidate-{snapshot.feature_snapshot_id}",
        purpose=purpose,
        label_contract_id={
            TrainingDatasetPurpose.CANDIDATE_SUCCESS: "candidate-success-label-v1",
            TrainingDatasetPurpose.ANNOTATION_DECISION: "annotation-decision-label-v1",
            TrainingDatasetPurpose.BUY_TIMING: "buy-timing-label-v1",
            TrainingDatasetPurpose.SELL_TIMING: "sell-timing-label-v1",
        }[purpose],
        feature_snapshot=snapshot,
    )


def _snapshot(
    feature_snapshot_id: str,
    selection_date: date,
    *,
    feature_set_version_id: str = "features-fixture-v1",
    feature_count: int = 2,
) -> FeatureSnapshot:
    as_of = datetime.combine(selection_date, datetime.min.time().replace(hour=16), tzinfo=UTC)
    values = {
        "close": Decimal("10"),
        "return_1": Decimal("0.01"),
    }
    if feature_count == 3:
        values["return_5"] = Decimal("0.02")
    return FeatureSnapshot(
        feature_snapshot_id=feature_snapshot_id,
        feature_set_version_id=feature_set_version_id,
        security_id="security-fixture",
        symbol="000001.SZ",
        selection_date=selection_date,
        as_of=as_of,
        available_at=as_of,
        input_session_start=date(2026, 2, 1),
        input_session_end=date(2026, 2, 28),
        source_snapshot_ids=("snapshot-fixture",),
        values=values,
    )


if __name__ == "__main__":
    unittest.main()
