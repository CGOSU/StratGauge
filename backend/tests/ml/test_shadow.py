import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.domain.models import ModelLifecycle
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.selection_training import BaselineModel
from stratgauge.ml.shadow import (
    ShadowScoreError,
    ShadowScoreStore,
    ShadowScoringInput,
    read_shadow_inputs,
    score_shadow_batch,
)


class ShadowScoringTests(unittest.TestCase):
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
