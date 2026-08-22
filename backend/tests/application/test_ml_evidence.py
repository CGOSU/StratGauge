import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from stratgauge.application.ml import (
    CalibrateFeatureProbabilitiesRequest,
    CompareFeatureModelsRequest,
    RegisterFeatureModelRequest,
    ScoreFeatureShadowRequest,
    TransitionFeatureModelRequest,
    ValidateFeatureWalkForwardRequest,
    run_calibrate_feature_probabilities,
    run_compare_feature_models,
    run_register_feature_model,
    run_score_feature_shadow,
    run_transition_feature_model,
    run_validate_feature_walk_forward,
)


class MLEvidenceApplicationTests(unittest.TestCase):
    def test_calibration_workflow_keeps_validation_only_inputs_explicit(self) -> None:
        training = type(
            "Training",
            (),
            {
                "model": object(),
                "predictions": object(),
                "predictions_sha256": "predictions-sha",
                "model_sha256": "model-sha",
                "manifest_sha256": "manifest-sha",
            },
        )()
        calibrated = object()
        with (
            patch(
                "stratgauge.application.ml.evidence.read_feature_lightgbm_output",
                return_value=training,
            ) as read,
            patch(
                "stratgauge.application.ml.evidence.calibrate_feature_lightgbm",
                return_value=calibrated,
            ) as calibrate,
            patch("stratgauge.application.ml.evidence.write_feature_calibration_result") as write,
        ):
            result = run_calibrate_feature_probabilities(
                CalibrateFeatureProbabilitiesRequest(
                    input_dir=Path("training"),
                    output_dir=Path("calibration"),
                    calibrated_at=datetime(2026, 1, 2, tzinfo=UTC),
                    minimum_validation_samples=25,
                )
            )

        read.assert_called_once_with(Path("training"))
        calibrate.assert_called_once_with(
            training.model,
            training.predictions,
            source_prediction_sha256="predictions-sha",
            source_model_sha256="model-sha",
            source_manifest_sha256="manifest-sha",
            calibrated_at=datetime(2026, 1, 2, tzinfo=UTC),
            minimum_validation_samples=25,
        )
        write.assert_called_once_with(Path("calibration"), calibrated)
        self.assertIs(result.result, calibrated)

    def test_registration_workflow_only_registers_as_challenger(self) -> None:
        store = Mock()
        registration = object()
        with (
            patch(
                "stratgauge.application.ml.evidence.FeatureModelRegistryStore",
                return_value=store,
            ) as registry,
            patch.object(store, "register", return_value=registration, create=True) as register,
        ):
            result = run_register_feature_model(
                RegisterFeatureModelRequest(
                    root=Path("root"),
                    training_dir=Path("training"),
                    calibration_dir=Path("calibration"),
                    registered_at=datetime(2026, 1, 2, tzinfo=UTC),
                    registered_by="operator",
                )
            )

        registry.assert_called_once_with(Path("root"))
        register.assert_called_once_with(
            Path("training"),
            calibration_output_dir=Path("calibration"),
            registered_at=datetime(2026, 1, 2, tzinfo=UTC),
            registered_by="operator",
        )
        self.assertIs(result.registration, registration)

    def test_transition_workflow_only_performs_requested_lifecycle_change(self) -> None:
        store = Mock()
        registration = object()
        with (
            patch(
                "stratgauge.application.ml.evidence.FeatureModelRegistryStore",
                return_value=store,
            ),
            patch.object(store, "transition", return_value=registration, create=True) as transition,
        ):
            result = run_transition_feature_model(
                TransitionFeatureModelRequest(
                    root=Path("root"),
                    registration_id="registration-1",
                    to_lifecycle="shadow",
                    changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                    reason="manual review",
                    operator="operator",
                )
            )

        transition.assert_called_once_with(
            "registration-1",
            "shadow",
            changed_at=datetime(2026, 1, 2, tzinfo=UTC),
            reason="manual review",
            operator="operator",
        )
        self.assertIs(result.registration, registration)

    def test_shadow_workflow_verifies_input_hash_and_writes_only_report(self) -> None:
        inputs = object()
        report = Mock()
        report.model_dump_json.return_value = "{}"
        registry = Mock()
        score_store = Mock()
        with (
            TemporaryDirectory() as directory,
            patch(
                "stratgauge.application.ml.evidence.read_feature_shadow_inputs",
                return_value=inputs,
            ) as read,
            patch(
                "stratgauge.application.ml.evidence.feature_shadow_input_sha256",
                return_value="input-sha",
            ) as input_hash,
            patch(
                "stratgauge.application.ml.evidence.FeatureModelRegistryStore",
                return_value=registry,
            ),
            patch(
                "stratgauge.application.ml.evidence.FeatureShadowScoreStore",
                return_value=score_store,
            ),
            patch(
                "stratgauge.application.ml.evidence.score_feature_shadow_batch",
                return_value=report,
            ) as score,
            patch("stratgauge.application.ml.evidence._write_shadow_report") as write,
        ):
            input_path = Path(directory) / "input.jsonl"
            input_path.write_bytes(b"input")
            result = run_score_feature_shadow(
                ScoreFeatureShadowRequest(
                    root=Path("root"),
                    registration_id="registration-1",
                    input_path=input_path,
                    source_input_sha256="input-sha",
                    scored_at=datetime(2026, 1, 2, tzinfo=UTC),
                    calibration_mode="none",
                    output_path=Path(directory) / "report.json",
                )
            )

        read.assert_called_once()
        input_hash.assert_called_once_with(inputs)
        score.assert_called_once_with(
            inputs,
            registry=registry,
            score_store=score_store,
            registration_id="registration-1",
            scored_at=datetime(2026, 1, 2, tzinfo=UTC),
            calibration_mode="none",
        )
        write.assert_called_once()
        self.assertIs(result.report, report)

    def test_walk_forward_workflow_parses_and_stores_one_report(self) -> None:
        split_input = object()
        registration = object()
        training = object()
        calibration = object()
        report = object()
        stored = object()
        registry = Mock()
        walk_forward_store = Mock()
        with TemporaryDirectory() as directory:
            folds_path = Path(directory) / "folds.json"
            folds_path.write_text(json.dumps([]), encoding="utf-8")
            with (
                patch(
                    "stratgauge.application.ml.evidence.read_training_feature_split",
                    return_value=split_input,
                ),
                patch(
                    "stratgauge.application.ml.evidence.FeatureModelRegistryStore",
                    return_value=registry,
                ),
                patch.object(
                    registry,
                    "load_outputs",
                    return_value=(registration, training, calibration),
                    create=True,
                ) as load_outputs,
                patch(
                    "stratgauge.application.ml.evidence.validate_feature_walk_forward",
                    return_value=report,
                ) as validate,
                patch(
                    "stratgauge.application.ml.evidence.FeatureWalkForwardReportStore",
                    return_value=walk_forward_store,
                ),
                patch.object(
                    walk_forward_store,
                    "write",
                    return_value=stored,
                    create=True,
                ) as write,
            ):
                result = run_validate_feature_walk_forward(
                    ValidateFeatureWalkForwardRequest(
                        root=Path("root"),
                        input_dir=Path("features"),
                        registration_id="registration-1",
                        folds_path=folds_path,
                        evaluated_at=datetime(2026, 1, 2, tzinfo=UTC),
                    )
                )

        load_outputs.assert_called_once_with("registration-1")
        validate.assert_called_once_with(
            split_input,
            registration=registration,
            training_output=training,
            calibration_output=calibration,
            folds=(),
            evaluated_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        write.assert_called_once_with(report)
        self.assertIs(result.report, stored)

    def test_comparison_workflow_keeps_reference_optional_and_returns_stored_hash(self) -> None:
        policy = object()
        report = type("Report", (), {"report_id": "report-1"})()
        comparison_store = Mock()
        with (
            TemporaryDirectory() as directory,
            patch(
                "stratgauge.application.ml.evidence.FeatureComparisonPolicy.model_validate_json",
                return_value=policy,
            ) as read_policy,
            patch(
                "stratgauge.application.ml.evidence.FeatureComparisonStore",
                return_value=comparison_store,
            ),
            patch(
                "stratgauge.application.ml.evidence.FeatureModelRegistryStore",
                return_value=object(),
            ) as registry,
            patch(
                "stratgauge.application.ml.evidence.FeatureWalkForwardReportStore",
                return_value=object(),
            ) as walk_forward,
            patch(
                "stratgauge.application.ml.evidence.compare_registered_feature_models",
                return_value=report,
            ) as compare,
            patch.object(
                comparison_store,
                "load_with_sha256",
                return_value=(report, "report-sha"),
                create=True,
            ) as load_with_hash,
        ):
            policy_path = Path(directory) / "policy.json"
            policy_path.write_bytes(b"policy")
            result = run_compare_feature_models(
                CompareFeatureModelsRequest(
                    root=Path("root"),
                    policy_path=policy_path,
                    challenger_registration_id="challenger",
                    challenger_report_id="challenger-report",
                    reference_registration_id=None,
                    reference_report_id=None,
                    compared_at=datetime(2026, 1, 2, tzinfo=UTC),
                    compared_by="operator",
                )
            )

        read_policy.assert_called_once_with(b"policy")
        registry.assert_called_once_with(Path("root"))
        walk_forward.assert_called_once_with(Path("root"))
        compare.assert_called_once_with(
            registry=registry.return_value,
            walk_forward_store=walk_forward.return_value,
            comparison_store=comparison_store,
            challenger_registration_id="challenger",
            challenger_report_id="challenger-report",
            reference_registration_id=None,
            reference_report_id=None,
            policy=policy,
            compared_at=datetime(2026, 1, 2, tzinfo=UTC),
            compared_by="operator",
        )
        load_with_hash.assert_called_once_with("report-1")
        self.assertIs(result.report, report)
        self.assertEqual(result.report_sha256, "report-sha")


if __name__ == "__main__":
    unittest.main()
