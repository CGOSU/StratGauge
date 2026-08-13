import unittest
from datetime import UTC, datetime
from decimal import Decimal
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
    SelectionPerformance,
    SelectionPerformancePoint,
    SelectionPerformanceStatus,
    SelectionReusableTagCategory,
    SelectionReusableTagSnapshot,
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

    def test_reusable_tags_are_idempotent_and_project_active_state(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            created_at = datetime(2026, 1, 5, 16, tzinfo=UTC)
            first = store.create_reusable_tag(
                category=SelectionReusableTagCategory.PATTERN,
                label="缩量企稳",
                created_at=created_at,
            )
            second = store.create_reusable_tag(
                category=SelectionReusableTagCategory.PATTERN,
                label="缩量企稳",
                created_at=created_at,
            )

            self.assertEqual(first, second)
            self.assertEqual(len(store.list_reusable_tags()), 1)
            self.assertEqual(store.list_reusable_tags()[0].label, "缩量企稳")
            self.assertTrue(store.list_reusable_tags()[0].active)

    def test_reusable_tag_rename_archive_restore_is_append_only(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            tag = store.create_reusable_tag(
                category=SelectionReusableTagCategory.POSITION,
                label="低位",
                created_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            )
            definition_path = Path(directory) / "tdx" / "annotation_tags" / f"{tag.tag_id}.json"
            definition_before = definition_path.read_bytes()

            store.set_reusable_tag_label(
                tag.tag_id,
                label="低位支撑",
                occurred_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )
            store.set_reusable_tag_status(
                tag.tag_id,
                SelectionLifecycleStatus.ARCHIVED,
                occurred_at=datetime(2026, 1, 5, 18, tzinfo=UTC),
            )
            self.assertEqual(store.list_reusable_tags(), ())
            archived = store.list_reusable_tags(include_archived=True)[0]
            self.assertEqual(archived.label, "低位支撑")
            self.assertFalse(archived.active)

            store.set_reusable_tag_status(
                tag.tag_id,
                SelectionLifecycleStatus.ACTIVE,
                occurred_at=datetime(2026, 1, 5, 19, tzinfo=UTC),
            )
            restored = store.list_reusable_tags()[0]
            self.assertEqual(restored.label, "低位支撑")
            self.assertTrue(restored.active)
            self.assertEqual(definition_path.read_bytes(), definition_before)
            self.assertEqual(len(store.list_reusable_tag_events(tag.tag_id)), 3)

    def test_reusable_tag_lifecycle_does_not_change_annotation_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            tag = store.create_reusable_tag(
                category=SelectionReusableTagCategory.PATTERN,
                label="缩量企稳",
                created_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            )
            annotation = SelectionAnnotation(
                annotation_id="annotation-tag-snapshot",
                candidate_id=parsed.candidates[0].candidate_id,
                reviewed_at=datetime(2026, 1, 5, 16, 1, tzinfo=UTC),
                recorded_at=datetime(2026, 1, 5, 16, 2, tzinfo=UTC),
                overall_score=4,
                outlook=SelectionAnnotationOutlook.BULLISH,
                decision=SelectionAnnotationDecision.PREPARE_BUY,
                reusable_tag_snapshots=(
                    SelectionReusableTagSnapshot(
                        tag_id=tag.tag_id,
                        category=tag.category,
                        label=tag.label,
                    ),
                ),
            )
            store.write_annotation(parsed.batch.batch_id, annotation)

            store.set_reusable_tag_label(
                tag.tag_id,
                label="缩量企稳确认",
                occurred_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )
            store.set_reusable_tag_status(
                tag.tag_id,
                SelectionLifecycleStatus.ARCHIVED,
                occurred_at=datetime(2026, 1, 5, 18, tzinfo=UTC),
            )

            loaded = store.list_annotations(annotation.candidate_id)
            self.assertEqual(loaded, (annotation,))
            self.assertEqual(loaded[0].reusable_tag_snapshots[0].label, "缩量企稳")

    def test_reusable_tag_events_require_monotonic_times_and_no_hard_delete(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            tag = store.create_reusable_tag(
                category=SelectionReusableTagCategory.SECTOR,
                label="行业强",
                created_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            )
            store.set_reusable_tag_status(
                tag.tag_id,
                SelectionLifecycleStatus.ARCHIVED,
                occurred_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )
            with self.assertRaisesRegex(SelectionStoreError, "already archived"):
                store.set_reusable_tag_status(
                    tag.tag_id,
                    SelectionLifecycleStatus.ARCHIVED,
                    occurred_at=datetime(2026, 1, 5, 18, tzinfo=UTC),
                )
            with self.assertRaisesRegex(SelectionStoreError, "after previous"):
                store.set_reusable_tag_status(
                    tag.tag_id,
                    SelectionLifecycleStatus.ACTIVE,
                    occurred_at=datetime(2026, 1, 5, 16, 30, tzinfo=UTC),
                )
            tag_files = tuple((Path(directory) / "tdx" / "annotation_tags").glob("*.json"))
            event_files = tuple((Path(directory) / "tdx" / "annotation_tag_events").glob("*.json"))
            self.assertEqual(len(tag_files), 1)
            self.assertEqual(len(event_files), 1)

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

    def test_performances_are_content_addressed_and_linked_to_maturity_tasks(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            performance = SelectionPerformance(
                candidate_id=parsed.candidates[0].candidate_id,
                batch_id=parsed.batch.batch_id,
                selection_date=parsed.batch.selection_date,
                status=SelectionPerformanceStatus.RESOLVED,
                evaluated_at=datetime(2026, 1, 10, 17, tzinfo=UTC),
                performance_policy_id="selection-performance-t1-t3-t5-t10-v1",
                anchor_session=datetime(2026, 1, 6, tzinfo=UTC).date(),
                anchor_price=Decimal("10"),
                points=(
                    SelectionPerformancePoint(
                        horizon_sessions=1,
                        session_date=datetime(2026, 1, 6, tzinfo=UTC).date(),
                        close_price=Decimal("10.2"),
                        return_from_anchor=Decimal("0.02"),
                        maximum_rise=Decimal("0.05"),
                        maximum_drawdown=Decimal("0"),
                    ),
                ),
                reason="forward_windows",
                maturity_task_id="maturity-task-1",
            )

            first = store.write_performance(performance)
            second = store.write_performance(performance)

            self.assertEqual(first, second)
            self.assertEqual(store.list_performances(performance.candidate_id), (performance,))
            self.assertEqual(
                store.find_performance_for_task("maturity-task-1", performance.candidate_id),
                performance,
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
