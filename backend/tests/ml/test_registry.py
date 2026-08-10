import json
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.domain.models import ModelLifecycle
from stratgauge.ml.comparison import (
    ComparisonMetricRule,
    ComparisonPolicy,
    ModelComparisonStore,
    compare_models,
)
from stratgauge.ml.registry import (
    ModelRegistryError,
    ModelRegistryStore,
    RegisteredModel,
)
from stratgauge.ml.selection_training import BaselineModel


class ModelRegistryStoreTests(unittest.TestCase):
    def test_registration_is_immutable_and_artifacts_are_verified(self) -> None:
        model = _model("baseline-fixture-v1")
        with TemporaryDirectory() as directory:
            store = ModelRegistryStore(directory)
            registered = _register(store, model)

            self.assertEqual(registered.lifecycle, ModelLifecycle.CHALLENGER)
            self.assertEqual(store.load(model.model_version_id), registered)
            self.assertTrue((store.artifact_dir / f"{model.model_version_id}.json").exists())
            self.assertTrue(
                (store.training_manifest_dir / f"{model.model_version_id}.json").exists()
            )
            self.assertEqual(
                store.register(
                    model,
                    **_artifacts(model),
                    registered_at=datetime(2026, 1, 1, tzinfo=UTC),
                    registered_by="test",
                ),
                registered,
            )

            artifact = store.artifact_dir / f"{model.model_version_id}.json"
            artifact.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ModelRegistryError, "hash does not match"):
                store.load(model.model_version_id)

    def test_activation_requires_shadow_and_matching_evaluation_hash(self) -> None:
        model = _model("baseline-fixture-v2")
        with TemporaryDirectory() as directory:
            store = ModelRegistryStore(directory)
            _register(store, model)
            with self.assertRaisesRegex(ModelRegistryError, "only a shadow"):
                store.promote(
                    model.model_version_id,
                    changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    reason="manual promotion",
                    operator="researcher",
                    evaluation_sha256="wrong",
                )
            store.transition(
                model.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                reason="shadow review",
                operator="researcher",
            )
            with self.assertRaisesRegex(ModelRegistryError, "does not match"):
                store.promote(
                    model.model_version_id,
                    changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                    reason="manual promotion",
                    operator="researcher",
                    evaluation_sha256="wrong",
                )
            comparison_report_id, comparison_report_sha256 = _comparison(store, model)
            active = store.promote(
                model.model_version_id,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="manual promotion",
                operator="researcher",
                evaluation_sha256=registered_evaluation_hash(model),
                comparison_report_id=comparison_report_id,
                comparison_report_sha256=comparison_report_sha256,
            )
            self.assertEqual(active.lifecycle, ModelLifecycle.ACTIVE)

    def test_same_policy_has_one_active_model_and_rollback_is_manual(self) -> None:
        first = _model("baseline-first-v1")
        second = _model("baseline-second-v1")
        with TemporaryDirectory() as directory:
            store = ModelRegistryStore(directory)
            _register(store, first)
            _register(store, second)
            store.transition(
                first.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                reason="shadow review",
                operator="researcher",
            )
            comparison_report_id, comparison_report_sha256 = _comparison(store, first)
            store.promote(
                first.model_version_id,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="manual activation",
                operator="researcher",
                evaluation_sha256=registered_evaluation_hash(first),
                comparison_report_id=comparison_report_id,
                comparison_report_sha256=comparison_report_sha256,
            )
            store.transition(
                second.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 3, tzinfo=UTC),
                reason="shadow review",
                operator="researcher",
            )
            comparison_report_id, comparison_report_sha256 = _comparison(
                store,
                second,
                incumbent=first,
            )
            with self.assertRaisesRegex(ModelRegistryError, "retire the compared active"):
                store.promote(
                    second.model_version_id,
                    changed_at=datetime(2026, 1, 4, tzinfo=UTC),
                    reason="manual activation",
                    operator="researcher",
                    evaluation_sha256=registered_evaluation_hash(second),
                    comparison_report_id=comparison_report_id,
                    comparison_report_sha256=comparison_report_sha256,
                )
            store.transition(
                second.model_version_id,
                ModelLifecycle.RETIRED,
                changed_at=datetime(2026, 1, 4, tzinfo=UTC),
                reason="rollback target prepared",
                operator="researcher",
            )
            rolled_back = store.rollback(
                second.model_version_id,
                changed_at=datetime(2026, 1, 5, tzinfo=UTC),
                reason="restore previous challenger",
                operator="researcher",
                evaluation_sha256=registered_evaluation_hash(second),
            )

            self.assertEqual(rolled_back.lifecycle, ModelLifecycle.ACTIVE)
            self.assertEqual(store.load(first.model_version_id).lifecycle, ModelLifecycle.RETIRED)
            self.assertEqual(
                store.active_models(tracking_policy_id="policy-v1")[0].model_version_id,
                second.model_version_id,
            )

    def test_invalid_transition_is_rejected(self) -> None:
        model = _model("baseline-fixture-v3")
        with TemporaryDirectory() as directory:
            store = ModelRegistryStore(directory)
            _register(store, model)
            with self.assertRaisesRegex(ModelRegistryError, "activation requires promote"):
                store.transition(
                    model.model_version_id,
                    ModelLifecycle.ACTIVE,
                    changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    reason="not enough evidence",
                    operator="researcher",
                )
            store.transition(
                model.model_version_id,
                ModelLifecycle.RETIRED,
                changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                reason="retire challenger",
                operator="researcher",
            )
            with self.assertRaisesRegex(ModelRegistryError, "invalid model lifecycle"):
                store.transition(
                    model.model_version_id,
                    ModelLifecycle.SHADOW,
                    changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                    reason="revive retired model",
                    operator="researcher",
                )


def _register(store: ModelRegistryStore, model: BaselineModel) -> RegisteredModel:
    return store.register(
        model,
        **_artifacts(model),
        registered_at=datetime(2026, 1, 1, tzinfo=UTC),
        registered_by="test",
    )


def _artifacts(model: BaselineModel) -> dict[str, bytes]:
    return {
        "model_artifact": model.model_dump_json(indent=2).encode("utf-8"),
        "evaluation_report": json.dumps(
            {
                "evaluation_version": "selection-model-evaluation-v1",
                "model_version_id": model.model_version_id,
                "model_type": model.model_type,
                "feature_set_version_id": model.feature_set_version_id,
                "tracking_policy_id": model.tracking_policy_id,
                "source_file_sha256": model.source_file_sha256,
                "calibrated": False,
                "metrics": {
                    "validation_log_loss": "0.5",
                    "validation_brier_score": "0.25",
                },
            },
            sort_keys=True,
        ).encode("utf-8"),
        "training_manifest": json.dumps(
            {
                "model_version_id": model.model_version_id,
                "feature_set_version_id": model.feature_set_version_id,
                "tracking_policy_id": model.tracking_policy_id,
                "source_file_sha256": model.source_file_sha256,
            },
            sort_keys=True,
        ).encode("utf-8"),
    }


def registered_evaluation_hash(model: BaselineModel) -> str:
    import hashlib

    return hashlib.sha256(_artifacts(model)["evaluation_report"]).hexdigest()


def _comparison(
    store: ModelRegistryStore,
    challenger: BaselineModel,
    *,
    incumbent: BaselineModel | None = None,
) -> tuple[str, str]:
    comparison_store = ModelComparisonStore(store.root)
    report = compare_models(
        registry=store,
        comparison_store=comparison_store,
        challenger_model_version_id=challenger.model_version_id,
        incumbent_model_version_id=(None if incumbent is None else incumbent.model_version_id),
        policy=ComparisonPolicy(
            comparison_policy_id="policy-fixture-v1",
            tracking_policy_id="policy-v1",
            rules=(
                ComparisonMetricRule(
                    metric_name="validation_log_loss",
                    direction="minimize",
                    maximum_value=Decimal("1"),
                ),
            ),
        ),
        compared_at=datetime(2026, 1, 3, 12, tzinfo=UTC)
        if incumbent is not None
        else datetime(2026, 1, 1, 12, tzinfo=UTC),
        compared_by="test",
    )
    _, report_sha256 = comparison_store.load_with_sha256(report.report_id)
    return report.report_id, report_sha256


def _model(model_version_id: str) -> BaselineModel:
    return BaselineModel(
        model_version_id=model_version_id,
        feature_set_version_id="features-fixture-v1",
        tracking_policy_id="policy-v1",
        positive_probability=Decimal("0.5"),
        train_row_count=10,
        train_positive_count=5,
        random_seed=0,
        source_file_sha256=f"source-{model_version_id}",
    )


if __name__ == "__main__":
    unittest.main()
