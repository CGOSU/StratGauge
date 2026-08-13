import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
    SelectionAnnotation,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionCandidateExclusion,
    SelectionLifecycleStatus,
    SelectionOutcome,
    SelectionOutcomeStatus,
    TdxSelectionImport,
)

EXPORTED_AT = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 1, 5, 15, 10, tzinfo=UTC)
CONTENT = "代码,名称,市场\n000001,平安银行,SZ\n".encode()


def parsed_import() -> TdxSelectionImport:
    return import_tdx_export(
        CONTENT,
        selection_date=EXPORTED_AT.date(),
        exported_at=EXPORTED_AT,
        imported_at=IMPORTED_AT,
        formula_name="公式 A",
    )


class SelectionImportStoreTests(unittest.TestCase):
    def test_formulas_are_created_idempotently_and_listed(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)

            first = store.create_formula(name="趋势选股", version="v3", description="收盘筛选")
            second = store.create_formula(name="趋势选股", version="v3", description="收盘筛选")

            self.assertEqual(first, second)
            self.assertEqual(store.list_formulas(), (first,))
            self.assertEqual(store.load_formula(first.formula_id), first)

    def test_lifecycle_events_archive_and_restore_without_mutating_import(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            formula = store.create_formula(name="公式 A")
            parsed = import_tdx_export(
                CONTENT,
                selection_date=EXPORTED_AT.date(),
                exported_at=EXPORTED_AT,
                imported_at=IMPORTED_AT,
                formula_name="公式 A",
                source_filename="selection.txt",
            )
            store.write(CONTENT, parsed)

            store.set_formula_status(
                formula.formula_id,
                SelectionLifecycleStatus.ARCHIVED,
                changed_at=IMPORTED_AT,
                reason="旧版本",
            )
            store.set_batch_status(
                parsed.batch.batch_id,
                SelectionLifecycleStatus.ARCHIVED,
                changed_at=IMPORTED_AT,
                reason="重复导入",
            )

            self.assertEqual(store.list_formulas(), ())
            self.assertEqual(store.list_manifests(), ())
            self.assertFalse(store.is_entity_active("formula", formula.formula_id))
            self.assertFalse(store.is_entity_active("batch", parsed.batch.batch_id))
            self.assertEqual(
                store.load_import(parsed.batch.batch_id).batch.source_filename, "selection.txt"
            )

            store.set_formula_status(
                formula.formula_id,
                SelectionLifecycleStatus.ACTIVE,
                changed_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            )
            store.set_batch_status(
                parsed.batch.batch_id,
                SelectionLifecycleStatus.ACTIVE,
                changed_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            )
            self.assertEqual(store.list_formulas(), (formula,))
            self.assertEqual(len(store.list_manifests()), 1)
            self.assertEqual(len(store.list_lifecycle_events("batch", parsed.batch.batch_id)), 2)

    def test_write_persists_raw_and_parsed_import_idempotently(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()

            first = store.write(CONTENT, parsed)
            second = store.write(CONTENT, parsed)

            self.assertEqual(first, second)
            self.assertTrue((Path(directory) / first.raw_path).exists())
            self.assertTrue((Path(directory) / first.import_path).exists())
            self.assertEqual(store.load_manifest(first.batch_id), first)

    def test_write_rejects_content_that_does_not_match_parsed_hash(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)

            with self.assertRaisesRegex(SelectionStoreError, "does not match"):
                store.write(b"different", parsed_import())

    def test_labels_are_append_only_and_listed_by_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            label = ManualSelectionLabel(
                candidate_id=parsed.candidates[0].candidate_id,
                decision=ManualSelectionDecision.WATCH,
                labeled_at=IMPORTED_AT,
                note="等待确认",
            )

            first = store.write_label(parsed.batch.batch_id, label)
            second = store.write_label(parsed.batch.batch_id, label)

            self.assertEqual(first, second)
            self.assertEqual(store.list_labels(label.candidate_id), (label,))

    def test_label_must_belong_to_the_batch(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            label = ManualSelectionLabel(
                candidate_id="other-batch:000001.SZ",
                decision=ManualSelectionDecision.PASS,
                labeled_at=IMPORTED_AT,
            )

            with self.assertRaisesRegex(SelectionStoreError, "does not belong"):
                store.write_label(parsed.batch.batch_id, label)

    def test_labels_after_freeze_deadline_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            label = ManualSelectionLabel(
                candidate_id=parsed.candidates[0].candidate_id,
                decision=ManualSelectionDecision.WATCH,
                labeled_at=datetime(2026, 1, 6, 9, tzinfo=UTC),
            )

            with self.assertRaisesRegex(SelectionStoreError, "freeze deadline"):
                store.write_label(parsed.batch.batch_id, label)

    def test_annotations_are_append_only_and_latest_is_point_in_time(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            candidate_id = parsed.candidates[0].candidate_id
            first = SelectionAnnotation(
                annotation_id="annotation-first",
                candidate_id=candidate_id,
                reviewed_at=datetime(2026, 1, 5, 15, 10, tzinfo=UTC),
                recorded_at=datetime(2026, 1, 5, 15, 10, 1, tzinfo=UTC),
                overall_score=3,
                outlook=SelectionAnnotationOutlook.NEUTRAL,
                decision=SelectionAnnotationDecision.WATCH,
                note="先观察",
            )
            second = first.model_copy(
                update={
                    "annotation_id": "annotation-second",
                    "reviewed_at": datetime(2026, 1, 6, 10, tzinfo=UTC),
                    "recorded_at": datetime(2026, 1, 6, 10, 1, tzinfo=UTC),
                    "overall_score": 4,
                    "outlook": SelectionAnnotationOutlook.BULLISH,
                    "decision": SelectionAnnotationDecision.PREPARE_BUY,
                }
            )

            self.assertEqual(store.write_annotation(parsed.batch.batch_id, first), first)
            self.assertEqual(store.write_annotation(parsed.batch.batch_id, second), second)
            self.assertEqual(store.list_annotations(candidate_id), (first, second))
            self.assertEqual(
                store.latest_annotation(
                    candidate_id,
                    recorded_before=datetime(2026, 1, 6, tzinfo=UTC),
                ),
                first,
            )
            self.assertEqual(store.latest_annotation(candidate_id), second)
            self.assertEqual(store.write_annotation(parsed.batch.batch_id, first), first)

    def test_exclusion_is_separate_from_annotation_and_requires_a_reason(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            exclusion = SelectionCandidateExclusion(
                exclusion_id="exclusion-first",
                candidate_id=parsed.candidates[0].candidate_id,
                excluded_at=datetime(2026, 1, 6, 9, tzinfo=UTC),
                recorded_at=datetime(2026, 1, 6, 9, 1, tzinfo=UTC),
                reason="导出代码无法复核",
            )

            self.assertEqual(
                store.write_exclusion(parsed.batch.batch_id, exclusion),
                exclusion,
            )
            self.assertEqual(store.list_exclusions(exclusion.candidate_id), (exclusion,))
            self.assertEqual(store.latest_exclusion(exclusion.candidate_id), exclusion)
            with self.assertRaises(ValueError):
                SelectionCandidateExclusion(
                    exclusion_id="exclusion-invalid",
                    candidate_id=exclusion.candidate_id,
                    excluded_at=exclusion.excluded_at,
                    recorded_at=exclusion.recorded_at,
                    reason=" ",
                )

    def test_outcomes_are_content_addressed_and_listed(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            outcome = SelectionOutcome(
                candidate_id=parsed.candidates[0].candidate_id,
                batch_id=parsed.batch.batch_id,
                selection_date=parsed.batch.selection_date,
                status=SelectionOutcomeStatus.UNRESOLVED,
                evaluated_at=IMPORTED_AT,
                horizon_sessions=5,
                reason="insufficient_horizon_data",
            )

            first = store.write_outcome(outcome)
            second = store.write_outcome(outcome)

            self.assertEqual(first, second)
            self.assertEqual(store.list_outcomes(outcome.candidate_id), (outcome,))


if __name__ == "__main__":
    unittest.main()
