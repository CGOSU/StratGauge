import json
import unittest
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.application.ml import (
    BuildTrainingFeatureSnapshotsRequest,
    CalibrateFeatureProbabilitiesRequest,
    CompareFeatureModelsRequest,
    FeatureLightGBMResult,
    JoinedTrainingFeaturesResult,
    JoinTrainingFeaturesRequest,
    PreparedTrainingDatasetResult,
    PrepareTrainingDatasetRequest,
    RegisterFeatureModelRequest,
    ScoreFeatureShadowRequest,
    TrainFeatureBaselineRequest,
    TrainFeatureLightGBMRequest,
    TrainingDatasetPurpose,
    TransitionFeatureModelRequest,
    ValidateFeatureWalkForwardRequest,
    run_build_training_feature_snapshots,
    run_calibrate_feature_probabilities,
    run_compare_feature_models,
    run_join_training_features,
    run_prepare_training_dataset,
    run_register_feature_model,
    run_score_feature_shadow,
    run_train_feature_baseline,
    run_train_feature_lightgbm,
    run_transition_feature_model,
    run_validate_feature_walk_forward,
)
from stratgauge.backtest.selection_dataset import (
    SelectionAnnotationDatasetRow,
    SelectionDatasetRow,
    serialize_annotation_dataset,
    serialize_selection_dataset,
)
from stratgauge.backtest.selection_ml_datasets import (
    SelectionBuyTimingRow,
    SelectionSellTimingRow,
    serialize_buy_timing_dataset,
    serialize_sell_timing_dataset,
)
from stratgauge.data_pipeline.quality import check_bars, quality_run_id
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_sources.base import (
    AdapterResult,
    IndexDailyRequest,
    MarketAdapterResult,
    MarketDailyRequest,
)
from stratgauge.domain.models import DailyBar, Exchange, Security
from stratgauge.domain.selections import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionOutcomeStatus,
    TdxSelectionImport,
)
from stratgauge.features.contract import default_feature_set
from stratgauge.features.store import FeatureSnapshotStore
from stratgauge.ml._hashing import sha256_bytes
from stratgauge.ml.training_datasets import (
    label_contract_id_for,
    ordered_labels_for,
)
from stratgauge.ml.training_feature_baseline import read_training_feature_split
from stratgauge.ml.training_feature_comparison import (
    FeatureComparisonMetricRule,
    FeatureComparisonPolicy,
)
from stratgauge.ml.training_feature_shadow import (
    FeatureShadowError,
    FeatureShadowInput,
    feature_shadow_input_sha256,
)
from stratgauge.ml.training_feature_walk_forward import FeatureWalkForwardFoldSpec


class FakeDataset:
    def __init__(
        self,
        matrix: list[list[float]],
        *,
        label: list[int],
        feature_name: list[str],
        free_raw_data: bool,
    ) -> None:
        self.matrix = matrix
        self.label = label
        self.feature_name = feature_name
        self.free_raw_data = free_raw_data


class FakeBooster:
    def __init__(self, *, multiclass: bool) -> None:
        self.multiclass = multiclass

    def predict(self, matrix: list[list[float]]) -> list[float] | list[list[float]]:
        if self.multiclass:
            return [[0.2, 0.5, 0.3] for _ in matrix]
        return [0.75 for _ in matrix]

    def model_to_string(self) -> str:
        return "feature-lightgbm-offline-fixture"


class FakeLightGBM:
    Dataset = FakeDataset
    multiclass = False
    train_calls: list[tuple[dict[str, object], FakeDataset, int]] = []

    @classmethod
    def train(
        cls,
        parameters: dict[str, object],
        dataset: FakeDataset,
        *,
        num_boost_round: int,
    ) -> FakeBooster:
        cls.train_calls.append((parameters, dataset, num_boost_round))
        return FakeBooster(multiclass=parameters.get("objective") == "multiclass")

    @classmethod
    def Booster(cls, *, model_str: str) -> FakeBooster:
        if model_str != "feature-lightgbm-offline-fixture":
            raise ValueError("unexpected offline LightGBM artifact")
        return FakeBooster(multiclass=cls.multiclass)


class FakeNumpy:
    @staticmethod
    def asarray(matrix: list[list[float]], *, dtype: type[float]) -> list[list[float]]:
        return matrix


class MLOfflineRehearsalTests(unittest.TestCase):
    def test_four_purpose_pipeline_and_binary_multiclass_evidence_are_offline(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            selection_root = root / "selection"
            market_root = root / "market"
            feature_root = root / "features"
            evidence_root = root / "evidence"
            selection_store = SelectionImportStore(selection_root)
            batches = _write_selection_batches(selection_store)
            market_snapshot_ids, benchmark_snapshot_ids = _write_market_evidence(market_root)
            raw_inputs = _write_raw_inputs(root / "raw", batches)
            prepared: dict[TrainingDatasetPurpose, PreparedTrainingDatasetResult] = {}
            joined: dict[TrainingDatasetPurpose, JoinedTrainingFeaturesResult] = {}
            lightgbm_results: dict[TrainingDatasetPurpose, FeatureLightGBMResult] = {}

            for purpose in TrainingDatasetPurpose:
                raw_path = raw_inputs[purpose]
                purpose_root = root / purpose.value
                preparation = run_prepare_training_dataset(
                    PrepareTrainingDatasetRequest(
                        input_path=raw_path,
                        purpose=purpose,
                        output_dir=purpose_root / "labels",
                        train_end=date(2026, 1, 10),
                        validation_end=date(2026, 1, 20),
                        label_available_through=datetime(2026, 2, 1, 17, tzinfo=UTC),
                        minimum_labeled_samples=1,
                    )
                )
                self.assertTrue(preparation.report.eligible)
                self.assertEqual(preparation.split.purged_rows, ())
                prepared[purpose] = preparation

                labels = (
                    preparation.split.train_rows
                    + preparation.split.validation_rows
                    + preparation.split.test_rows
                )
                feature_report = run_build_training_feature_snapshots(
                    BuildTrainingFeatureSnapshotsRequest(
                        labels=labels,
                        selection_root=selection_root,
                        market_root=market_root,
                        feature_root=feature_root,
                        report_path=purpose_root / "feature-report" / "report.json",
                        market_snapshot_ids=market_snapshot_ids,
                        benchmark_snapshot_ids=benchmark_snapshot_ids,
                        as_of_cutoff=datetime(2026, 2, 2, tzinfo=UTC),
                    )
                ).report
                self.assertEqual(feature_report.source_label_row_count, len(labels))
                self.assertEqual(feature_report.failure_count, 0)
                self.assertEqual(feature_report.snapshot_count, len(labels))
                self.assertEqual(len(feature_report.source_label_dataset_sha256), 64)
                feature_store = FeatureSnapshotStore(feature_root)
                for mapping in feature_report.mappings:
                    snapshot = feature_store.load(mapping.feature_snapshot_id)
                    self.assertEqual(snapshot.selection_date, mapping.observation_date)
                    self.assertLess(snapshot.input_session_end, mapping.observation_date)
                    self.assertEqual(snapshot.as_of, mapping.as_of)

                feature_set = default_feature_set()
                joined_result = run_join_training_features(
                    JoinTrainingFeaturesRequest(
                        labels_dir=purpose_root / "labels",
                        feature_root=feature_root,
                        feature_set_version_id=feature_set.feature_set_version_id,
                        feature_snapshot_ids=tuple(
                            mapping.feature_snapshot_id for mapping in feature_report.mappings
                        ),
                        output_dir=purpose_root / "joined",
                    )
                )
                joined_split = read_training_feature_split(joined_result.output_dir)
                self.assertEqual(joined_split.report.failure_count, 0)
                self.assertEqual(
                    joined_split.report.source_label_row_count,
                    len(labels),
                )
                self.assertEqual(
                    joined_split.report.source_label_dataset_sha256,
                    preparation.prepared.source_file_sha256,
                )
                joined[purpose] = joined_result

                baseline = run_train_feature_baseline(
                    TrainFeatureBaselineRequest(
                        input_dir=purpose_root / "joined",
                        output_dir=purpose_root / "baseline",
                        random_seed=7,
                        minimum_labeled_samples=1,
                    )
                )
                self.assertEqual(
                    baseline.result.model.train_row_count,
                    len(joined_split.train_rows),
                )
                self.assertTrue((purpose_root / "baseline" / "manifest.json").is_file())

                FakeLightGBM.train_calls = []
                FakeLightGBM.multiclass = len(ordered_labels_for(purpose)) == 3
                with patch(
                    "stratgauge.ml.training_feature_lightgbm.helpers.import_module",
                    side_effect=_fake_import,
                ):
                    trained = run_train_feature_lightgbm(
                        TrainFeatureLightGBMRequest(
                            input_dir=purpose_root / "joined",
                            output_dir=purpose_root / "lightgbm",
                            random_seed=7,
                            minimum_labeled_samples=1,
                        )
                    )
                self.assertEqual(len(FakeLightGBM.train_calls), 1)
                self.assertEqual(
                    len(FakeLightGBM.train_calls[0][1].matrix),
                    len(joined_split.train_rows),
                )
                self.assertEqual(
                    trained.result.model.row_counts,
                    {
                        "train": len(joined_split.train_rows),
                        "validation": len(joined_split.validation_rows),
                        "test": len(joined_split.test_rows),
                    },
                )
                self.assertTrue((purpose_root / "lightgbm" / "manifest.json").is_file())
                lightgbm_results[purpose] = trained

            for purpose in (
                TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                TrainingDatasetPurpose.ANNOTATION_DECISION,
            ):
                self._run_full_evidence_path(
                    purpose,
                    root=root,
                    evidence_root=evidence_root,
                    joined=joined,
                    lightgbm_results=lightgbm_results,
                )

            candidate_model_dir = root / TrainingDatasetPurpose.CANDIDATE_SUCCESS.value / "baseline"
            with self.assertRaises(FileExistsError):
                run_train_feature_baseline(
                    TrainFeatureBaselineRequest(
                        input_dir=root / TrainingDatasetPurpose.CANDIDATE_SUCCESS.value / "joined",
                        output_dir=candidate_model_dir,
                        minimum_labeled_samples=1,
                    )
                )

    def _run_full_evidence_path(
        self,
        purpose: TrainingDatasetPurpose,
        *,
        root: Path,
        evidence_root: Path,
        joined: dict[TrainingDatasetPurpose, JoinedTrainingFeaturesResult],
        lightgbm_results: dict[TrainingDatasetPurpose, FeatureLightGBMResult],
    ) -> None:
        purpose_root = root / purpose.value
        joined_result = joined[purpose]
        training_result = lightgbm_results[purpose]
        joined_split = read_training_feature_split(joined_result.output_dir)
        validation_count = len(joined_split.validation_rows)
        test_count = len(joined_split.test_rows)
        calibration_dir = evidence_root / purpose.value / "calibration"
        trained_dir = purpose_root / "lightgbm"
        calibrated = run_calibrate_feature_probabilities(
            CalibrateFeatureProbabilitiesRequest(
                input_dir=trained_dir,
                output_dir=calibration_dir,
                calibrated_at=datetime(2026, 2, 10, 17, tzinfo=UTC),
                minimum_validation_samples=validation_count,
            )
        )
        self.assertEqual(calibrated.result.model.calibration_row_count, validation_count)
        self.assertEqual(calibrated.result.model.test_row_count, test_count)
        for label in set(item.label for item in calibrated.result.model.bins):
            self.assertEqual(
                sum(
                    item.sample_count
                    for item in calibrated.result.model.bins
                    if item.label is label
                ),
                validation_count,
            )
        self.assertTrue((calibration_dir / "manifest.json").is_file())

        registered = run_register_feature_model(
            RegisterFeatureModelRequest(
                root=evidence_root,
                training_dir=trained_dir,
                calibration_dir=calibration_dir,
                registered_at=datetime(2026, 2, 11, 9, tzinfo=UTC),
                registered_by="offline-rehearsal",
            )
        )
        self.assertEqual(registered.registration.lifecycle, "challenger")
        feature_snapshot = FeatureSnapshotStore(root / "features").load(
            joined_split.train_rows[0].feature_snapshot_id
        )
        shadow_input = FeatureShadowInput(
            candidate_id=f"offline-shadow-{purpose.value}",
            purpose=purpose,
            label_contract_id=label_contract_id_for(purpose),
            feature_snapshot=feature_snapshot,
        )
        shadow_input_path = evidence_root / purpose.value / "shadow-input.jsonl"
        shadow_input_path.parent.mkdir(parents=True, exist_ok=True)
        shadow_input_path.write_text(
            shadow_input.model_dump_json() + "\n", encoding="utf-8", newline="\n"
        )
        with self.assertRaisesRegex(FeatureShadowError, "shadow lifecycle"):
            run_score_feature_shadow(
                ScoreFeatureShadowRequest(
                    root=evidence_root,
                    registration_id=registered.registration.registration_id,
                    input_path=shadow_input_path,
                    source_input_sha256=feature_shadow_input_sha256((shadow_input,)),
                    scored_at=datetime(2026, 2, 12, 17, tzinfo=UTC),
                    calibration_mode="registered",
                    output_path=evidence_root / purpose.value / "pre-shadow.json",
                )
            )

        transitioned = run_transition_feature_model(
            TransitionFeatureModelRequest(
                root=evidence_root,
                registration_id=registered.registration.registration_id,
                to_lifecycle="shadow",
                changed_at=datetime(2026, 2, 11, 10, tzinfo=UTC),
                reason="offline rehearsal manual shadow gate",
                operator="offline-rehearsal",
            )
        )
        self.assertEqual(transitioned.registration.lifecycle, "shadow")
        shadow_report_path = evidence_root / purpose.value / "shadow-report.json"
        FakeLightGBM.multiclass = len(ordered_labels_for(purpose)) == 3
        with (
            patch(
                "stratgauge.ml.training_feature_shadow.helpers.import_module",
                side_effect=_fake_import,
            ),
            patch(
                "stratgauge.ml.training_feature_walk_forward.helpers_b.import_module",
                side_effect=_fake_import,
            ),
        ):
            shadow = run_score_feature_shadow(
                ScoreFeatureShadowRequest(
                    root=evidence_root,
                    registration_id=registered.registration.registration_id,
                    input_path=shadow_input_path,
                    source_input_sha256=feature_shadow_input_sha256((shadow_input,)),
                    scored_at=datetime(2026, 2, 12, 17, tzinfo=UTC),
                    calibration_mode="registered",
                    output_path=shadow_report_path,
                )
            )
            self.assertFalse(shadow.report.used_for_decision)
            self.assertEqual(shadow.report.calibration_mode, "registered")

            fold = FeatureWalkForwardFoldSpec(
                fold_id=f"offline-{purpose.value}",
                train_start=date(2026, 1, 1),
                train_end=date(2026, 1, 10),
                validation_start=date(2026, 1, 15),
                validation_end=date(2026, 1, 20),
            )
            folds_path = evidence_root / purpose.value / "folds.json"
            folds_path.write_text(
                json.dumps([fold.model_dump(mode="json")], ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            walk_forward = run_validate_feature_walk_forward(
                ValidateFeatureWalkForwardRequest(
                    root=evidence_root,
                    input_dir=joined_result.output_dir,
                    registration_id=registered.registration.registration_id,
                    folds_path=folds_path,
                    evaluated_at=datetime(2026, 2, 13, 17, tzinfo=UTC),
                )
            )
        self.assertEqual(walk_forward.report.final_test_row_count, test_count)
        self.assertEqual(
            sum(item.validation_row_count for item in walk_forward.report.folds),
            validation_count,
        )
        self.assertTrue(
            all(
                prediction.observation_date
                < min(row.observation_date for row in joined_split.test_rows)
                for item in walk_forward.report.folds
                for prediction in item.predictions
            )
        )
        self.assertTrue((evidence_root / "models" / "feature-registry").is_dir())

        policy = FeatureComparisonPolicy(
            comparison_policy_id=f"offline-policy-{purpose.value}",
            purpose=purpose,
            label_contract_id=label_contract_id_for(purpose),
            feature_set_version_id=joined_split.manifest.feature_set_version_id,
            feature_names=joined_split.manifest.feature_names,
            class_labels=ordered_labels_for(purpose),
            rules=(
                FeatureComparisonMetricRule(
                    metric_name="validation_coverage",
                    direction="maximize",
                    minimum_value=Decimal("1"),
                ),
            ),
            require_reference_model=False,
            require_calibrated=True,
        )
        policy_path = evidence_root / purpose.value / "comparison-policy.json"
        policy_path.write_text(
            policy.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        comparison = run_compare_feature_models(
            CompareFeatureModelsRequest(
                root=evidence_root,
                policy_path=policy_path,
                challenger_registration_id=registered.registration.registration_id,
                challenger_report_id=walk_forward.report.report_id,
                reference_registration_id=None,
                reference_report_id=None,
                compared_at=datetime(2026, 2, 14, 17, tzinfo=UTC),
                compared_by="offline-rehearsal",
            )
        )
        self.assertTrue(comparison.report.gate_passed)
        self.assertEqual(
            comparison.report.challenger_metrics["validation_row_count"],
            Decimal(validation_count),
        )
        self.assertEqual(len(comparison.report.reference_metrics), 0)
        self.assertEqual(len(comparison.report_sha256), 64)
        self.assertTrue((evidence_root / "models" / "feature-registry" / "comparisons").is_dir())

        # The application evidence reader is what proves the on-disk source hashes are usable.
        self.assertEqual(len(training_result.result.model.source_label_dataset_sha256), 64)
        self.assertEqual(len(sha256_bytes((calibration_dir / "manifest.json").read_bytes())), 64)


def _fake_import(name: str) -> object:
    if name == "lightgbm":
        return FakeLightGBM
    if name == "numpy":
        return FakeNumpy
    raise ModuleNotFoundError(name)


def _write_selection_batches(store: SelectionImportStore) -> dict[date, TdxSelectionImport]:
    dates = (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 15),
        date(2026, 1, 16),
        date(2026, 1, 17),
        date(2026, 1, 25),
    )
    result: dict[date, TdxSelectionImport] = {}
    for selection_date in dates:
        content = f"代码,名称,市场\n000001,离线{selection_date.day},SZ\n".encode()
        imported = import_tdx_export(
            content,
            selection_date=selection_date,
            exported_at=datetime.combine(
                selection_date,
                datetime.min.time().replace(hour=9),
                tzinfo=UTC,
            ),
            imported_at=datetime.combine(
                selection_date,
                datetime.min.time().replace(hour=10),
                tzinfo=UTC,
            ),
            formula_name="offline-rehearsal",
        )
        store.write(content, imported)
        result[selection_date] = imported
    return result


def _write_raw_inputs(
    raw_root: Path,
    batches: dict[date, TdxSelectionImport],
) -> dict[TrainingDatasetPurpose, Path]:
    raw_root.mkdir(parents=True, exist_ok=True)
    paths: dict[TrainingDatasetPurpose, Path] = {}
    raw_content = {
        TrainingDatasetPurpose.CANDIDATE_SUCCESS: serialize_selection_dataset(
            _candidate_rows(batches)
        ),
        TrainingDatasetPurpose.ANNOTATION_DECISION: serialize_annotation_dataset(
            _annotation_rows(batches)
        ),
        TrainingDatasetPurpose.BUY_TIMING: serialize_buy_timing_dataset(_buy_rows(batches)),
        TrainingDatasetPurpose.SELL_TIMING: serialize_sell_timing_dataset(_sell_rows(batches)),
    }
    for purpose, content in raw_content.items():
        path = raw_root / f"{purpose.value}.jsonl"
        path.write_text(content, encoding="utf-8", newline="\n")
        paths[purpose] = path
    return paths


def _candidate_rows(batches: dict[date, TdxSelectionImport]) -> tuple[SelectionDatasetRow, ...]:
    specs = (
        (date(2026, 1, 5), SelectionOutcomeStatus.WIN),
        (date(2026, 1, 6), SelectionOutcomeStatus.LOSS),
        (date(2026, 1, 15), SelectionOutcomeStatus.WIN),
        (date(2026, 1, 16), SelectionOutcomeStatus.LOSS),
        (date(2026, 1, 25), SelectionOutcomeStatus.WIN),
    )
    return tuple(
        SelectionDatasetRow(
            candidate_id=batches[selection_date].candidates[0].candidate_id,
            batch_id=batches[selection_date].batch.batch_id,
            symbol="000001.SZ",
            selection_date=selection_date.isoformat(),
            source_filename=None,
            formula_id=None,
            formula_name="offline-rehearsal",
            formula_version=None,
            manual_decision=None,
            manual_labeled_at=None,
            tracking_policy_id="tracking-v1",
            outcome_policy_id="outcome-v1",
            cost_schedule_id="cost-v1",
            horizon_sessions=1,
            position_notional="1000",
            outcome_status=status,
            outcome_evaluated_at=_label_available_at(selection_date).isoformat(),
            net_return="0.01" if status is SelectionOutcomeStatus.WIN else "-0.01",
            source_snapshot_ids=(f"source-{selection_date.isoformat()}",),
        )
        for selection_date, status in specs
    )


def _annotation_rows(
    batches: dict[date, TdxSelectionImport],
) -> tuple[SelectionAnnotationDatasetRow, ...]:
    specs = (
        (date(2026, 1, 5), SelectionAnnotationDecision.DISCARD),
        (date(2026, 1, 6), SelectionAnnotationDecision.WATCH),
        (date(2026, 1, 7), SelectionAnnotationDecision.PREPARE_BUY),
        (date(2026, 1, 15), SelectionAnnotationDecision.DISCARD),
        (date(2026, 1, 16), SelectionAnnotationDecision.WATCH),
        (date(2026, 1, 17), SelectionAnnotationDecision.PREPARE_BUY),
        (date(2026, 1, 25), SelectionAnnotationDecision.WATCH),
    )
    rows: list[SelectionAnnotationDatasetRow] = []
    for index, (reviewed_date, decision) in enumerate(specs):
        reviewed_at = datetime.combine(
            reviewed_date,
            datetime.min.time().replace(hour=15),
            tzinfo=UTC,
        )
        candidate = batches[reviewed_date].candidates[0]
        rows.append(
            SelectionAnnotationDatasetRow(
                annotation_id=f"annotation-offline-{index}",
                candidate_id=candidate.candidate_id,
                batch_id=candidate.batch_id,
                symbol=candidate.symbol,
                selection_date=reviewed_date.isoformat(),
                source_filename=None,
                formula_id=None,
                formula_name="offline-rehearsal",
                formula_version=None,
                annotation_reviewed_at=reviewed_at.isoformat(),
                annotation_recorded_at=(reviewed_at + timedelta(minutes=5)).isoformat(),
                overall_score=4,
                outlook=SelectionAnnotationOutlook.BULLISH,
                decision=decision,
                position_tags=(),
                pattern_tags=(),
                price_volume_tags=(),
                sector_tags=(),
                taxonomy_version=SELECTION_ANNOTATION_TAXONOMY_VERSION,
                annotation_note=None,
                tracking_policy_id=None,
                outcome_policy_id=None,
                cost_schedule_id=None,
                horizon_sessions=None,
                position_notional=None,
                outcome_status=None,
                outcome_evaluated_at=None,
                net_return=None,
                source_snapshot_ids=(),
            )
        )
    return tuple(rows)


def _buy_rows(batches: dict[date, TdxSelectionImport]) -> tuple[SelectionBuyTimingRow, ...]:
    specs = (
        (date(2026, 1, 5), True),
        (date(2026, 1, 6), False),
        (date(2026, 1, 15), True),
        (date(2026, 1, 16), False),
        (date(2026, 1, 25), False),
    )
    rows: list[SelectionBuyTimingRow] = []
    for index, (observed_date, purchased) in enumerate(specs):
        reviewed_at = datetime.combine(
            observed_date,
            datetime.min.time().replace(hour=15),
            tzinfo=UTC,
        )
        evaluated_at = _label_available_at(observed_date)
        candidate = batches[observed_date].candidates[0]
        rows.append(
            SelectionBuyTimingRow(
                annotation_id=f"buy-annotation-offline-{index}",
                candidate_id=candidate.candidate_id,
                batch_id=candidate.batch_id,
                symbol=candidate.symbol,
                selection_date=observed_date,
                annotation_reviewed_at=reviewed_at,
                annotation_recorded_at=reviewed_at + timedelta(minutes=5),
                evaluated_at=evaluated_at,
                overall_score=4,
                outlook=SelectionAnnotationOutlook.BULLISH,
                decision=SelectionAnnotationDecision.WATCH,
                observation_end=observed_date + timedelta(days=1),
                window_sessions=1,
                purchased_within_window=purchased,
                order_id=f"buy-order-{index}" if purchased else None,
                fill_id=f"buy-fill-{index}" if purchased else None,
                filled_at=(
                    datetime.combine(
                        observed_date + timedelta(days=1),
                        datetime.min.time().replace(hour=10),
                        tzinfo=UTC,
                    )
                    if purchased
                    else None
                ),
                fill_price=Decimal("10") if purchased else None,
                fill_quantity=Decimal("100") if purchased else None,
                source_order_ids=(f"buy-order-{index}",) if purchased else (),
            )
        )
    return tuple(rows)


def _sell_rows(batches: dict[date, TdxSelectionImport]) -> tuple[SelectionSellTimingRow, ...]:
    specs = (
        (date(2026, 1, 5), True),
        (date(2026, 1, 6), False),
        (date(2026, 1, 15), True),
        (date(2026, 1, 16), False),
        (date(2026, 1, 25), False),
    )
    rows: list[SelectionSellTimingRow] = []
    for index, (exposure_date, sold) in enumerate(specs):
        candidate = batches[exposure_date].candidates[0]
        rows.append(
            SelectionSellTimingRow(
                candidate_id=candidate.candidate_id,
                batch_id=candidate.batch_id,
                symbol=candidate.symbol,
                selection_date=exposure_date,
                exposure_session=exposure_date,
                evaluated_at=_label_available_at(exposure_date),
                sell_window_end=exposure_date + timedelta(days=1),
                window_sessions=1,
                sold_within_window=sold,
                exposure_quantity=Decimal("100"),
                average_cost=Decimal("10"),
                buy_order_ids=(f"sell-buy-order-{index}",),
                buy_fill_ids=(f"sell-buy-fill-{index}",),
                sell_order_ids=(f"sell-order-{index}",) if sold else (),
                sell_fill_ids=(f"sell-fill-{index}",) if sold else (),
            )
        )
    return tuple(rows)


def _label_available_at(observation_date: date) -> datetime:
    return datetime.combine(
        observation_date + timedelta(days=1),
        datetime.min.time().replace(hour=17),
        tzinfo=UTC,
    )


def _write_market_evidence(market_root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    market_store = SnapshotStore(market_root)
    candidate = Security(
        security_id="security-000001.SZ",
        local_code="000001",
        symbol="000001.SZ",
        exchange=Exchange.SZ,
    )
    benchmark = Security(
        security_id="index-000300.SH",
        local_code="000300",
        symbol="000300.SH",
        exchange=Exchange.SH,
    )
    market_ids: list[str] = []
    benchmark_ids: list[str] = []
    current = date(2025, 12, 1)
    end = date(2026, 1, 24)
    while current <= end:
        available_at = datetime.combine(
            current,
            datetime.min.time().replace(hour=16),
            tzinfo=UTC,
        )
        candidate_bar = _daily_bar(candidate, current, available_at, Decimal("10"))
        candidate_request = MarketDailyRequest(
            session_date=current,
            universe_snapshot_id="offline-universe",
            expected_security_ids=(candidate.security_id,),
        )
        market_result = MarketAdapterResult(
            provider="offline-fixture",
            adapter_version="offline-v1",
            request=candidate_request,
            fetched_at=available_at,
            raw_records=(),
            bars=(candidate_bar,),
        )
        market_manifest = market_store.write(market_result)
        _approve(
            market_store,
            market_manifest.snapshot_id,
            (candidate_bar,),
            candidate.security_id,
            current,
        )
        market_ids.append(market_manifest.snapshot_id)

        benchmark_bar = _daily_bar(benchmark, current, available_at, Decimal("100"))
        benchmark_request = IndexDailyRequest(
            security=benchmark,
            start_date=current,
            end_date=current,
        )
        benchmark_result = AdapterResult(
            provider="offline-fixture",
            adapter_version="offline-v1",
            request=benchmark_request,
            fetched_at=available_at,
            raw_records=(),
            bars=(benchmark_bar,),
        )
        benchmark_manifest = market_store.write(benchmark_result)
        _approve(
            market_store,
            benchmark_manifest.snapshot_id,
            (benchmark_bar,),
            benchmark.security_id,
            current,
        )
        benchmark_ids.append(benchmark_manifest.snapshot_id)
        current += timedelta(days=1)
    return tuple(market_ids), tuple(benchmark_ids)


def _approve(
    store: SnapshotStore,
    snapshot_id: str,
    bars: Sequence[DailyBar],
    security_id: str,
    session: date,
) -> None:
    quality = check_bars(
        bars,
        snapshot_id=snapshot_id,
        expected_sessions=(session,),
        expected_security_ids=(security_id,),
    )
    if not quality.approved:
        raise AssertionError(f"offline market fixture was rejected: {quality.issues}")
    run_id = quality_run_id(quality)
    store.save_quality_report(quality, run_id)
    store.update_status(snapshot_id, SnapshotStatus.APPROVED, run_id)


def _daily_bar(
    security: Security,
    session: date,
    available_at: datetime,
    close: Decimal,
) -> DailyBar:
    return DailyBar(
        security_id=security.security_id,
        symbol=security.symbol,
        session_date=session,
        provider="offline-fixture",
        provider_symbol=security.symbol,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000"),
        amount=close * Decimal("1000"),
        event_time=datetime.combine(
            session,
            datetime.min.time().replace(hour=15),
            tzinfo=UTC,
        ),
        available_at=available_at,
        ingested_at=available_at,
        quality_state="approved",
    )


if __name__ == "__main__":
    unittest.main()
