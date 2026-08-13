import hashlib
import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_comparison import (
    FeatureComparisonError,
    FeatureComparisonMetricRule,
    FeatureComparisonModelEvidence,
    FeatureComparisonPolicy,
    FeatureComparisonStore,
    aggregate_feature_walk_forward_metrics,
    compare_feature_model_evidence,
)
from stratgauge.ml.training_feature_lightgbm import (
    FeatureLightGBMEvaluationEvidence,
    FeatureLightGBMModel,
)
from stratgauge.ml.training_feature_lightgbm_calibration import FeatureLightGBMOutput
from stratgauge.ml.training_feature_registry import FeatureModelRegistration, _registration_id
from stratgauge.ml.training_feature_walk_forward import (
    FeatureWalkForwardFoldReport,
    FeatureWalkForwardMetrics,
    FeatureWalkForwardPrediction,
    FeatureWalkForwardReport,
)


class TrainingFeatureComparisonTests(unittest.TestCase):
    def test_absolute_gate_and_aggregate_metrics_are_explicit(self) -> None:
        challenger = _evidence("challenger", log_loss=0.2, accuracy=0.8)
        policy = _policy(
            FeatureComparisonMetricRule(
                metric_name="validation_log_loss",
                direction="minimize",
                maximum_value=Decimal("0.3"),
            )
        )
        report = compare_feature_model_evidence(
            challenger=challenger,
            reference=None,
            policy=policy,
            compared_at=datetime(2026, 3, 1, tzinfo=UTC),
            compared_by="researcher",
        )
        self.assertTrue(report.gate_passed)
        self.assertEqual(report.challenger_metrics["validation_log_loss"], Decimal("0.2"))
        self.assertEqual(
            aggregate_feature_walk_forward_metrics(challenger.walk_forward)["validation_row_count"],
            Decimal("1"),
        )

        failing_policy = _policy(
            FeatureComparisonMetricRule(
                metric_name="validation_accuracy",
                direction="maximize",
                minimum_value=Decimal("0.9"),
            )
        )
        failed = compare_feature_model_evidence(
            challenger=challenger,
            reference=None,
            policy=failing_policy,
            compared_at=datetime(2026, 3, 1, tzinfo=UTC),
            compared_by="researcher",
        )
        self.assertFalse(failed.gate_passed)
        self.assertIn("below minimum", failed.checks[0].reason)

    def test_relative_regression_and_missing_metrics_are_explicit(self) -> None:
        challenger = _evidence("challenger", log_loss=0.4, accuracy=0.7)
        reference = _evidence("reference", log_loss=0.2, accuracy=0.8)
        report = compare_feature_model_evidence(
            challenger=challenger,
            reference=reference,
            policy=_policy(
                FeatureComparisonMetricRule(
                    metric_name="validation_log_loss",
                    direction="minimize",
                    max_relative_regression=Decimal("0.5"),
                )
            ),
            compared_at=datetime(2026, 3, 1, tzinfo=UTC),
            compared_by="researcher",
        )
        self.assertFalse(report.gate_passed)
        self.assertEqual(report.checks[0].relative_regression, Decimal("1"))
        self.assertIn("relative regression", report.checks[0].reason)

        missing = compare_feature_model_evidence(
            challenger=challenger,
            reference=None,
            policy=_policy(
                FeatureComparisonMetricRule(
                    metric_name="not_a_metric",
                    direction="minimize",
                    maximum_value=Decimal("1"),
                )
            ),
            compared_at=datetime(2026, 3, 1, tzinfo=UTC),
            compared_by="researcher",
        )
        self.assertFalse(missing.gate_passed)
        self.assertIn("metric is missing", missing.checks[0].reason)

    def test_scope_purpose_columns_and_hashes_must_match(self) -> None:
        challenger = _evidence("challenger", log_loss=0.2, accuracy=0.8)
        wrong_purpose = _evidence(
            "reference",
            purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
            log_loss=0.2,
            accuracy=0.8,
        )
        with self.assertRaisesRegex(FeatureComparisonError, "purpose"):
            compare_feature_model_evidence(
                challenger=challenger,
                reference=wrong_purpose,
                policy=_policy(
                    FeatureComparisonMetricRule(
                        metric_name="validation_log_loss",
                        direction="minimize",
                        maximum_value=Decimal("1"),
                    )
                ),
                compared_at=datetime(2026, 3, 1, tzinfo=UTC),
                compared_by="researcher",
            )

        wrong_hash = _evidence("reference", log_loss=0.2, accuracy=0.8, source_hash="9" * 64)
        with self.assertRaisesRegex(FeatureComparisonError, "source labels"):
            compare_feature_model_evidence(
                challenger=challenger,
                reference=wrong_hash,
                policy=_policy(
                    FeatureComparisonMetricRule(
                        metric_name="validation_log_loss",
                        direction="minimize",
                        maximum_value=Decimal("1"),
                    )
                ),
                compared_at=datetime(2026, 3, 1, tzinfo=UTC),
                compared_by="researcher",
            )

    def test_calibration_and_time_boundaries_are_gates(self) -> None:
        challenger = _evidence("challenger", log_loss=0.2, accuracy=0.8, calibrated=False)
        with self.assertRaisesRegex(FeatureComparisonError, "comparison time precedes"):
            compare_feature_model_evidence(
                challenger=challenger,
                reference=None,
                policy=_policy(
                    FeatureComparisonMetricRule(
                        metric_name="validation_log_loss",
                        direction="minimize",
                        maximum_value=Decimal("1"),
                    ),
                    require_calibrated=True,
                ),
                compared_at=datetime(2026, 1, 1, tzinfo=UTC),
                compared_by="researcher",
            )

        failed = compare_feature_model_evidence(
            challenger=challenger,
            reference=None,
            policy=_policy(
                FeatureComparisonMetricRule(
                    metric_name="validation_log_loss",
                    direction="minimize",
                    maximum_value=Decimal("1"),
                ),
                require_calibrated=True,
            ),
            compared_at=datetime(2026, 3, 1, tzinfo=UTC),
            compared_by="researcher",
        )
        self.assertFalse(failed.gate_passed)
        self.assertEqual(failed.calibration_reason, "challenger registration is not calibrated")

    def test_report_store_is_immutable_and_comparison_does_not_change_lifecycle(self) -> None:
        challenger = _evidence("challenger", log_loss=0.2, accuracy=0.8)
        report = compare_feature_model_evidence(
            challenger=challenger,
            reference=None,
            policy=_policy(
                FeatureComparisonMetricRule(
                    metric_name="validation_log_loss",
                    direction="minimize",
                    maximum_value=Decimal("1"),
                )
            ),
            compared_at=datetime(2026, 3, 1, tzinfo=UTC),
            compared_by="researcher",
        )
        self.assertEqual(challenger.registration.lifecycle, "challenger")
        with TemporaryDirectory() as directory:
            store = FeatureComparisonStore(directory)
            self.assertEqual(store.write(report), report)
            self.assertEqual(store.write(report), report)
            path = store.comparison_dir / f"{report.report_id}.json"
            path.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(FeatureComparisonError, "invalid"):
                store.load(report.report_id)

    def test_reference_is_required_for_relative_gate(self) -> None:
        with self.assertRaisesRegex(FeatureComparisonError, "reference model"):
            compare_feature_model_evidence(
                challenger=_evidence("challenger", log_loss=0.2, accuracy=0.8),
                reference=None,
                policy=_policy(
                    FeatureComparisonMetricRule(
                        metric_name="validation_log_loss",
                        direction="minimize",
                        max_relative_regression=Decimal("0.1"),
                    )
                ),
                compared_at=datetime(2026, 3, 1, tzinfo=UTC),
                compared_by="researcher",
            )


def _policy(
    rule: FeatureComparisonMetricRule, *, require_calibrated: bool = False
) -> FeatureComparisonPolicy:
    return FeatureComparisonPolicy(
        comparison_policy_id="feature-policy-v1",
        purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
        label_contract_id=label_contract_id_for(TrainingDatasetPurpose.CANDIDATE_SUCCESS),
        feature_set_version_id="features-fixture-v1",
        feature_names=("close", "return_1"),
        class_labels=(TrainingLabel.NOT_SUCCESS, TrainingLabel.SUCCESS),
        rules=(rule,),
        require_reference_model=False,
        require_calibrated=require_calibrated,
    )


def _evidence(
    name: str,
    *,
    purpose: TrainingDatasetPurpose = TrainingDatasetPurpose.CANDIDATE_SUCCESS,
    log_loss: float,
    accuracy: float,
    calibrated: bool = False,
    source_hash: str = "b" * 64,
) -> FeatureComparisonModelEvidence:
    labels = (
        (TrainingLabel.NOT_SUCCESS, TrainingLabel.SUCCESS)
        if purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS
        else (TrainingLabel.DISCARD, TrainingLabel.WATCH, TrainingLabel.PREPARE_BUY)
    )
    feature_names = ("close", "return_1")
    model = FeatureLightGBMModel(
        model_version_id=f"model-{name}",
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        feature_set_version_id="features-fixture-v1",
        feature_names=feature_names,
        class_labels=labels,
        train_row_count=len(labels),
        train_target_counts={label.value: 1 for label in labels},
        random_seed=0,
        minimum_labeled_samples=1,
        feature_dataset_manifest_sha256="f" * 64,
        source_label_dataset_sha256=source_hash,
        label_split_manifest_sha256="c" * 64,
        artifact_sha256="a" * 64,
        parameters={
            "objective": "binary" if len(labels) == 2 else "multiclass",
            **({"num_class": 3} if len(labels) == 3 else {}),
        },
        row_counts={"train": len(labels), "validation": 2, "test": 1},
        purged_row_count=0,
    )
    registration = FeatureModelRegistration(
        registration_id=_registration_id(model.model_version_id, None),
        model_version_id=model.model_version_id,
        purpose=purpose,
        label_contract_id=model.label_contract_id,
        feature_set_version_id=model.feature_set_version_id,
        feature_names=feature_names,
        class_labels=labels,
        training_model_sha256="1" * 64,
        training_runtime_artifact_sha256=model.artifact_sha256,
        training_metrics_sha256="2" * 64,
        training_predictions_sha256="3" * 64,
        training_manifest_sha256="4" * 64,
        evaluation_sha256="2" * 64,
        feature_dataset_manifest_sha256="f" * 64,
        source_label_dataset_sha256=source_hash,
        label_split_manifest_sha256="c" * 64,
        registered_at=datetime(2026, 2, 1, tzinfo=UTC),
        registered_by="fixture",
    )
    fold = _fold(purpose, name, log_loss, accuracy)
    walk_forward = FeatureWalkForwardReport(
        report_id="pending",
        registration_id=registration.registration_id,
        registration_lifecycle="challenger",
        model_version_id=model.model_version_id,
        purpose=purpose,
        label_contract_id=model.label_contract_id,
        feature_set_version_id=model.feature_set_version_id,
        feature_names=feature_names,
        class_labels=labels,
        registered_at=registration.registered_at,
        evaluated_at=datetime(2026, 2, 15, tzinfo=UTC),
        registered_model_sha256=registration.training_model_sha256,
        registered_training_manifest_sha256=registration.training_manifest_sha256,
        feature_dataset_manifest_sha256=registration.feature_dataset_manifest_sha256,
        source_label_dataset_sha256=source_hash,
        label_split_manifest_sha256=registration.label_split_manifest_sha256,
        final_test_start=date(2026, 2, 20),
        final_test_row_count=1,
        final_test_feature_partition_sha256="5" * 64,
        final_test_label_partition_sha256="6" * 64,
        purged_row_count=0,
        purged_partition_sha256="7" * 64,
        folds=(fold,),
    )
    report_identity = walk_forward.model_dump(mode="json")
    report_identity.pop("report_id")
    report_id = (
        "feature-walk-forward-"
        + hashlib.sha256(
            json.dumps(
                report_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
    )
    walk_forward = walk_forward.model_copy(update={"report_id": report_id})
    metrics = FeatureLightGBMEvaluationEvidence(
        model_version_id=model.model_version_id,
        purpose=purpose,
        label_contract_id=model.label_contract_id,
        feature_set_version_id=model.feature_set_version_id,
        feature_dataset_manifest_sha256=model.feature_dataset_manifest_sha256,
        metrics=(),
    )
    training = FeatureLightGBMOutput(
        model=model,
        metrics=metrics,
        predictions=(),
        manifest={},
        model_sha256=registration.training_model_sha256,
        metrics_sha256=registration.training_metrics_sha256,
        predictions_sha256=registration.training_predictions_sha256,
        manifest_sha256=registration.training_manifest_sha256,
    )
    return FeatureComparisonModelEvidence(
        registration=registration,
        training=training,
        calibration=None,
        walk_forward=walk_forward,
        walk_forward_sha256="8" * 64,
    )


def _fold(
    purpose: TrainingDatasetPurpose,
    name: str,
    log_loss: float,
    accuracy: float,
) -> FeatureWalkForwardFoldReport:
    labels = (
        (TrainingLabel.NOT_SUCCESS, TrainingLabel.SUCCESS)
        if purpose is TrainingDatasetPurpose.CANDIDATE_SUCCESS
        else (TrainingLabel.DISCARD, TrainingLabel.WATCH, TrainingLabel.PREPARE_BUY)
    )
    target = labels[0]
    probabilities = {label.value: Decimal("0") for label in labels}
    probabilities[target.value] = Decimal("1")
    prediction = FeatureWalkForwardPrediction(
        fold_id="fold-1",
        sample_id="sample-1",
        candidate_id="candidate-1",
        symbol="000001.SZ",
        observation_date=date(2026, 2, 10),
        label_available_at=datetime(2026, 2, 10, 17, tzinfo=UTC),
        target=target,
        predicted_label=target,
        predicted_probabilities=probabilities,
        feature_snapshot_id="feature-1",
        source_label_row_sha256="9" * 64,
    )
    metric = FeatureWalkForwardMetrics(
        row_count=1,
        coverage=Decimal("1"),
        target_counts={label.value: (1 if label is target else 0) for label in labels},
        mean_predicted_probabilities=probabilities,
        log_loss=log_loss,
        normalized_brier_score=0.1,
        accuracy=Decimal(str(accuracy)),
    )
    return FeatureWalkForwardFoldReport(
        fold_id="fold-1",
        train_start=date(2026, 1, 1),
        train_end=date(2026, 2, 1),
        validation_start=date(2026, 2, 10),
        validation_end=date(2026, 2, 10),
        train_candidate_row_count=2,
        train_row_count=2,
        validation_row_count=1,
        purged_train_row_count=0,
        train_target_counts={
            label.value: (1 if index < 2 else 0) for index, label in enumerate(labels)
        },
        metrics=metric,
        predictions=(prediction,),
    )


if __name__ == "__main__":
    unittest.main()
