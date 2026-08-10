import hashlib
import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.domain.models import ModelLifecycle
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml.calibration import CalibratedModel, IsotonicCalibrationBin
from stratgauge.ml.lightgbm_training import LightGBMModel
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.selection_training import BaselineModel
from stratgauge.ml.shadow import (
    ShadowScoreError,
    ShadowScoreStore,
    ShadowScoringInput,
    read_shadow_inputs,
    score_shadow_batch,
)


class FakeNumpy:
    @staticmethod
    def asarray(matrix: list[list[float]], *, dtype: type[float]) -> list[list[float]]:
        return matrix


class FakeBooster:
    def predict(self, matrix: list[list[float]]) -> list[float]:
        return [0.7 for _ in matrix]


class FakeLightGBM:
    @staticmethod
    def Booster(*, model_str: str) -> FakeBooster:
        if model_str != "runtime":
            raise ValueError("unexpected runtime")
        return FakeBooster()


class ShadowScoringTests(unittest.TestCase):
    def test_lightgbm_shadow_score_supports_optional_calibration(self) -> None:
        model = _lightgbm_model()
        calibration = CalibratedModel(
            calibration_id="calibration-fixture",
            base_model_version_id=model.model_version_id,
            feature_set_version_id=model.feature_set_version_id,
            tracking_policy_id=model.tracking_policy_id,
            calibrated_at=datetime(2026, 1, 4, tzinfo=UTC),
            calibration_row_count=100,
            calibration_positive_count=50,
            calibration_date_start=date(2025, 1, 1),
            calibration_date_end=date(2025, 12, 31),
            test_row_count=10,
            source_prediction_sha256="predictions-hash",
            source_model_manifest_sha256="manifest-hash",
            bins=(
                IsotonicCalibrationBin(
                    lower_probability=Decimal("0"),
                    upper_probability=Decimal("0.9"),
                    calibrated_probability=Decimal("0.8"),
                    sample_count=100,
                    positive_count=80,
                ),
            ),
        )
        with TemporaryDirectory() as directory:
            registry = ModelRegistryStore(directory)
            registry.register(
                model,
                model_artifact=model.model_dump_json(indent=2).encode("utf-8"),
                runtime_artifact=b"runtime",
                evaluation_report=b"{}",
                training_manifest=json.dumps(
                    {
                        "model_version_id": model.model_version_id,
                        "feature_set_version_id": model.feature_set_version_id,
                        "tracking_policy_id": model.tracking_policy_id,
                        "source_file_sha256": model.source_file_sha256,
                    }
                ).encode("utf-8"),
                registered_at=datetime(2026, 1, 1, tzinfo=UTC),
                registered_by="test",
            )
            registry.transition(
                model.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="shadow started",
                operator="test",
            )
            with patch(
                "stratgauge.ml.shadow.import_module",
                side_effect=lambda name: FakeLightGBM if name == "lightgbm" else FakeNumpy,
            ):
                report = score_shadow_batch(
                    (_input("candidate-1", _snapshot("feature-1", date(2026, 1, 5))),),
                    registry=registry,
                    score_store=ShadowScoreStore(directory),
                    model_version_id=model.model_version_id,
                    scored_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
                    source_input_sha256="input-hash",
                    calibration_model=calibration,
                )

            score = ShadowScoreStore(directory).load_score(report.score_ids[0])
            self.assertEqual(score.uncalibrated_model_score, Decimal("0.7"))
            self.assertEqual(score.calibrated_model_score, Decimal("0.8"))
            self.assertTrue(score.calibrated)
            self.assertFalse(score.used_for_decision)

    def test_shadow_score_is_separate_and_unavailable_feature_is_reported(self) -> None:
        model = _model()
        with TemporaryDirectory() as directory:
            registry = ModelRegistryStore(directory)
            _register_shadow(registry, model)
            store = ShadowScoreStore(directory)
            scored_at = datetime(2026, 1, 5, 17, tzinfo=UTC)
            inputs = (
                _input("candidate-1", _snapshot("feature-1", date(2026, 1, 5))),
                _input("candidate-2", _snapshot("feature-2", date(2026, 1, 6))),
            )

            report = score_shadow_batch(
                inputs,
                registry=registry,
                score_store=store,
                model_version_id=model.model_version_id,
                scored_at=scored_at,
                source_input_sha256="input-hash",
            )

            self.assertEqual(len(report.score_ids), 1)
            self.assertEqual(len(report.failures), 1)
            score = store.load_score(report.score_ids[0])
            self.assertEqual(score.uncalibrated_model_score, Decimal("0.5"))
            self.assertFalse(score.calibrated)
            self.assertFalse(score.used_for_decision)
            self.assertEqual(score.model_lifecycle, ModelLifecycle.SHADOW.value)
            self.assertEqual(score.tracking_policy_id, "policy-v1")

    def test_shadow_input_rejects_outcome_labels(self) -> None:
        payload = {
            "candidate_id": "candidate-1",
            "feature_snapshot": _snapshot("feature-1", date(2026, 1, 5)).model_dump(mode="json"),
            "label": "win",
        }
        with self.assertRaisesRegex(ShadowScoreError, "must not contain outcome labels"):
            read_shadow_inputs((json.dumps(payload) + "\n").encode("utf-8"))

    def test_shadow_scoring_requires_shadow_lifecycle(self) -> None:
        model = _model()
        with TemporaryDirectory() as directory:
            registry = ModelRegistryStore(directory)
            _register_shadow(registry, model)
            registry.transition(
                model.model_version_id,
                ModelLifecycle.RETIRED,
                changed_at=datetime(2026, 1, 3, tzinfo=UTC),
                reason="retired before scoring",
                operator="test",
            )
            with self.assertRaisesRegex(ShadowScoreError, "shadow lifecycle"):
                score_shadow_batch(
                    (_input("candidate-1", _snapshot("feature-1", date(2026, 1, 5))),),
                    registry=registry,
                    score_store=ShadowScoreStore(directory),
                    model_version_id=model.model_version_id,
                    scored_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
                    source_input_sha256="input-hash",
                )

    def test_same_shadow_run_is_idempotent(self) -> None:
        model = _model()
        with TemporaryDirectory() as directory:
            registry = ModelRegistryStore(directory)
            _register_shadow(registry, model)
            inputs = (_input("candidate-1", _snapshot("feature-1", date(2026, 1, 5))),)
            store = ShadowScoreStore(directory)
            first = score_shadow_batch(
                inputs,
                registry=registry,
                score_store=store,
                model_version_id=model.model_version_id,
                scored_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
                source_input_sha256="input-hash",
            )
            second = score_shadow_batch(
                inputs,
                registry=registry,
                score_store=store,
                model_version_id=model.model_version_id,
                scored_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
                source_input_sha256="input-hash",
            )

            self.assertEqual(first, second)


def _register_shadow(registry: ModelRegistryStore, model: BaselineModel) -> None:
    evaluation = b'{"log_loss": 0.5}'
    registry.register(
        model,
        model_artifact=model.model_dump_json(indent=2).encode("utf-8"),
        evaluation_report=evaluation,
        training_manifest=json.dumps(
            {
                "model_version_id": model.model_version_id,
                "feature_set_version_id": model.feature_set_version_id,
                "tracking_policy_id": model.tracking_policy_id,
                "source_file_sha256": model.source_file_sha256,
            },
            sort_keys=True,
        ).encode("utf-8"),
        registered_at=datetime(2026, 1, 1, tzinfo=UTC),
        registered_by="test",
    )
    registry.transition(
        model.model_version_id,
        ModelLifecycle.SHADOW,
        changed_at=datetime(2026, 1, 2, tzinfo=UTC),
        reason="shadow started",
        operator="test",
    )


def _lightgbm_model() -> LightGBMModel:
    runtime_hash = hashlib.sha256(b"runtime").hexdigest()
    return LightGBMModel(
        model_version_id="lightgbm-shadow-v1",
        feature_set_version_id="features-fixture-v1",
        tracking_policy_id="policy-v1",
        feature_names=("return_1",),
        train_row_count=10,
        train_positive_count=5,
        random_seed=0,
        source_file_sha256="source-hash",
        artifact_sha256=runtime_hash,
        parameters={"num_boost_round": 50},
    )


def _model() -> BaselineModel:
    return BaselineModel(
        model_version_id="baseline-shadow-v1",
        feature_set_version_id="features-fixture-v1",
        tracking_policy_id="policy-v1",
        positive_probability=Decimal("0.5"),
        train_row_count=10,
        train_positive_count=5,
        random_seed=0,
        source_file_sha256="source-hash",
    )


def _input(candidate_id: str, snapshot: FeatureSnapshot) -> ShadowScoringInput:
    return ShadowScoringInput(candidate_id=candidate_id, feature_snapshot=snapshot)


def _snapshot(feature_snapshot_id: str, as_of_date: date) -> FeatureSnapshot:
    as_of = datetime.combine(as_of_date, datetime.min.time().replace(hour=16), tzinfo=UTC)
    return FeatureSnapshot(
        feature_snapshot_id=feature_snapshot_id,
        feature_set_version_id="features-fixture-v1",
        security_id="security-1",
        symbol="000001.SZ",
        selection_date=as_of_date,
        as_of=as_of,
        available_at=as_of,
        input_session_start=date(2025, 12, 1),
        input_session_end=date(2025, 12, 31),
        source_snapshot_ids=("snapshot-1",),
        values={"return_1": Decimal("0.01")},
    )


if __name__ == "__main__":
    unittest.main()
