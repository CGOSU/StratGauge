import hashlib
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
from stratgauge.ml.evaluation import ModelEvaluationEvidence
from stratgauge.ml.registry import ModelRegistryError, ModelRegistryStore
from stratgauge.ml.selection_training import BaselineModel


class ModelComparisonTests(unittest.TestCase):
    def test_missing_metric_fails_gate_and_cannot_promote(self) -> None:
        model = _model("challenger-missing-metric")
        with TemporaryDirectory() as directory:
            registry = ModelRegistryStore(directory)
            _register(
                registry,
                model,
                metrics={"validation_log_loss": Decimal("0.4")},
            )
            registry.transition(
                model.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="shadow review",
                operator="test",
            )
            comparison_store = ModelComparisonStore(directory)
            report = compare_models(
                registry=registry,
                comparison_store=comparison_store,
                challenger_model_version_id=model.model_version_id,
                policy=_policy(
                    ComparisonMetricRule(
                        metric_name="max_drawdown",
                        direction="minimize",
                        maximum_value=Decimal("0.2"),
                    )
                ),
                compared_at=datetime(2026, 1, 3, tzinfo=UTC),
                compared_by="test",
            )

            self.assertFalse(report.gate_passed)
            self.assertIn("missing", report.checks[0].reason)
            _, report_sha256 = comparison_store.load_with_sha256(report.report_id)
            with self.assertRaisesRegex(ModelRegistryError, "did not pass"):
                registry.promote(
                    model.model_version_id,
                    changed_at=datetime(2026, 1, 4, tzinfo=UTC),
                    reason="manual promotion",
                    operator="test",
                    evaluation_sha256=_evaluation_hash(
                        model, {"validation_log_loss": Decimal("0.4")}
                    ),
                    comparison_report_id=report.report_id,
                    comparison_report_sha256=report_sha256,
                )

    def test_relative_regression_and_calibration_are_explicit_gates(self) -> None:
        incumbent = _model("incumbent-v1")
        challenger = _model("challenger-v1")
        with TemporaryDirectory() as directory:
            registry = ModelRegistryStore(directory)
            _register(
                registry,
                incumbent,
                metrics={"validation_log_loss": Decimal("0.2")},
                calibrated=True,
            )
            registry.transition(
                incumbent.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="shadow review",
                operator="test",
            )
            first_policy = _policy(
                ComparisonMetricRule(
                    metric_name="validation_log_loss",
                    direction="minimize",
                    maximum_value=Decimal("1"),
                )
            )
            comparison_store = ModelComparisonStore(directory)
            first_report = compare_models(
                registry=registry,
                comparison_store=comparison_store,
                challenger_model_version_id=incumbent.model_version_id,
                policy=first_policy,
                compared_at=datetime(2026, 1, 3, tzinfo=UTC),
                compared_by="test",
            )
            _, first_report_sha256 = comparison_store.load_with_sha256(first_report.report_id)
            registry.promote(
                incumbent.model_version_id,
                changed_at=datetime(2026, 1, 4, tzinfo=UTC),
                reason="initial activation",
                operator="test",
                evaluation_sha256=_evaluation_hash(
                    incumbent, {"validation_log_loss": Decimal("0.2")}, calibrated=True
                ),
                comparison_report_id=first_report.report_id,
                comparison_report_sha256=first_report_sha256,
            )
            _register(
                registry,
                challenger,
                metrics={"validation_log_loss": Decimal("0.5")},
                calibrated=False,
            )
            registry.transition(
                challenger.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 5, tzinfo=UTC),
                reason="shadow review",
                operator="test",
            )
            report = compare_models(
                registry=registry,
                comparison_store=comparison_store,
                challenger_model_version_id=challenger.model_version_id,
                incumbent_model_version_id=incumbent.model_version_id,
                policy=ComparisonPolicy(
                    comparison_policy_id="policy-v2",
                    tracking_policy_id="policy-v1",
                    require_calibrated=True,
                    rules=(
                        ComparisonMetricRule(
                            metric_name="validation_log_loss",
                            direction="minimize",
                            max_regression=Decimal("0.1"),
                        ),
                    ),
                ),
                compared_at=datetime(2026, 1, 6, tzinfo=UTC),
                compared_by="test",
            )

            self.assertFalse(report.gate_passed)
            self.assertIn("regression", report.checks[0].reason)
            self.assertEqual(report.calibration_reason, "challenger evaluation is not calibrated")

    def test_tampered_comparison_report_is_rejected(self) -> None:
        model = _model("challenger-tampered-report")
        with TemporaryDirectory() as directory:
            registry = ModelRegistryStore(directory)
            _register(
                registry,
                model,
                metrics={"validation_log_loss": Decimal("0.4")},
            )
            registry.transition(
                model.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="shadow review",
                operator="test",
            )
            comparison_store = ModelComparisonStore(directory)
            report = compare_models(
                registry=registry,
                comparison_store=comparison_store,
                challenger_model_version_id=model.model_version_id,
                policy=_policy(
                    ComparisonMetricRule(
                        metric_name="validation_log_loss",
                        direction="minimize",
                        maximum_value=Decimal("1"),
                    )
                ),
                compared_at=datetime(2026, 1, 3, tzinfo=UTC),
                compared_by="test",
            )
            report_path = comparison_store.comparison_dir / f"{report.report_id}.json"
            report_path.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ModelRegistryError, "cannot be verified"):
                registry.promote(
                    model.model_version_id,
                    changed_at=datetime(2026, 1, 4, tzinfo=UTC),
                    reason="manual promotion",
                    operator="test",
                    evaluation_sha256=_evaluation_hash(
                        model, {"validation_log_loss": Decimal("0.4")}
                    ),
                    comparison_report_id=report.report_id,
                    comparison_report_sha256="not-the-hash",
                )


def _policy(rule: ComparisonMetricRule) -> ComparisonPolicy:
    return ComparisonPolicy(
        comparison_policy_id="policy-fixture-v1",
        tracking_policy_id="policy-v1",
        rules=(rule,),
    )


def _register(
    registry: ModelRegistryStore,
    model: BaselineModel,
    *,
    metrics: dict[str, Decimal | None],
    calibrated: bool = False,
) -> None:
    evaluation = ModelEvaluationEvidence(
        model_version_id=model.model_version_id,
        model_type=model.model_type,
        feature_set_version_id=model.feature_set_version_id,
        tracking_policy_id=model.tracking_policy_id,
        source_file_sha256=model.source_file_sha256,
        calibrated=calibrated,
        metrics=metrics,
    )
    registry.register(
        model,
        model_artifact=model.model_dump_json(indent=2).encode("utf-8"),
        evaluation_report=evaluation.model_dump_json(indent=2).encode("utf-8"),
        training_manifest=(
            "{"
            f'"model_version_id":"{model.model_version_id}",'
            f'"feature_set_version_id":"{model.feature_set_version_id}",'
            f'"tracking_policy_id":"{model.tracking_policy_id}",'
            f'"source_file_sha256":"{model.source_file_sha256}"'
            "}"
        ).encode(),
        registered_at=datetime(2026, 1, 1, tzinfo=UTC),
        registered_by="test",
    )


def _evaluation_hash(
    model: BaselineModel,
    metrics: dict[str, Decimal | None],
    *,
    calibrated: bool = False,
) -> str:
    evaluation = ModelEvaluationEvidence(
        model_version_id=model.model_version_id,
        model_type=model.model_type,
        feature_set_version_id=model.feature_set_version_id,
        tracking_policy_id=model.tracking_policy_id,
        source_file_sha256=model.source_file_sha256,
        calibrated=calibrated,
        metrics=metrics,
    )
    return hashlib.sha256(evaluation.model_dump_json(indent=2).encode("utf-8")).hexdigest()


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
