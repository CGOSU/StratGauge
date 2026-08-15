import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stratgauge.data_pipeline.selection_index import SelectionIndexError
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.selections import (
    SelectionLifecycleStatus,
    SelectionOutcome,
    SelectionOutcomeStatus,
    SelectionPerformance,
    SelectionPerformanceStatus,
    SelectionReusableTagCategory,
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


class SelectionReadIndexTests(unittest.TestCase):
    def test_keyed_reads_use_projection_and_preserve_source_files(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            parsed = parsed_import()
            store.write(CONTENT, parsed)
            candidate_id = parsed.candidates[0].candidate_id
            outcome = SelectionOutcome(
                candidate_id=candidate_id,
                batch_id=parsed.batch.batch_id,
                selection_date=parsed.batch.selection_date,
                status=SelectionOutcomeStatus.UNRESOLVED,
                evaluated_at=IMPORTED_AT,
                horizon_sessions=5,
                reason="insufficient_horizon_data",
                maturity_task_id="maturity-task-index",
            )
            performance = SelectionPerformance(
                candidate_id=candidate_id,
                batch_id=parsed.batch.batch_id,
                selection_date=parsed.batch.selection_date,
                status=SelectionPerformanceStatus.UNRESOLVED,
                evaluated_at=IMPORTED_AT,
                performance_policy_id="policy-index",
                reason="insufficient_horizon_data",
                maturity_task_id="maturity-task-index",
            )
            store.write_outcome(outcome)
            store.write_performance(performance)
            formula = store.create_formula(name="公式 A")
            lifecycle = store.set_formula_status(
                formula.formula_id,
                SelectionLifecycleStatus.ARCHIVED,
                changed_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            )
            tag = store.create_reusable_tag(
                category=SelectionReusableTagCategory.PATTERN,
                label="缩量企稳",
                created_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            )
            tag_event = store.set_reusable_tag_status(
                tag.tag_id,
                SelectionLifecycleStatus.ARCHIVED,
                occurred_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )

            with (
                patch.object(
                    store, "_list_all_outcomes", side_effect=AssertionError("outcome scan")
                ),
                patch.object(
                    store,
                    "_list_all_performances",
                    side_effect=AssertionError("performance scan"),
                ),
                patch.object(
                    Path,
                    "glob",
                    side_effect=AssertionError("selection event scan"),
                ),
                patch.object(store, "_load_outcome", wraps=store._load_outcome) as load_outcome,
            ):
                self.assertEqual(
                    store.find_outcome_for_task("maturity-task-index", candidate_id), outcome
                )
                self.assertEqual(
                    store.list_outcomes_for_candidates((candidate_id,)),
                    {candidate_id: (outcome,)},
                )
                self.assertEqual(
                    store.find_performance_for_task("maturity-task-index", candidate_id),
                    performance,
                )
                self.assertEqual(
                    store.list_lifecycle_events("formula", formula.formula_id), (lifecycle,)
                )
                self.assertEqual(store.list_reusable_tag_events(tag.tag_id), (tag_event,))
                self.assertEqual(load_outcome.call_count, 2)

    def test_rebuild_is_idempotent_and_recovers_existing_files(self) -> None:
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
                maturity_task_id="maturity-task-rebuild",
            )
            store.write_outcome(outcome)
            outcome_path = next((Path(directory) / "tdx" / "outcomes").glob("*.json"))
            before = outcome_path.read_bytes()

            first_stats = store.rebuild_selection_index()
            second_stats = store.rebuild_selection_index()
            self.assertEqual(first_stats, second_stats)
            self.assertEqual(first_stats.outcomes, 1)
            self.assertEqual(outcome_path.read_bytes(), before)

            index_path = Path(directory) / "tdx" / "selection-index.duckdb"
            index_path.unlink()
            restored = SelectionImportStore(directory)
            self.assertEqual(
                restored.find_outcome_for_task("maturity-task-rebuild", outcome.candidate_id),
                outcome,
            )

    def test_projection_failure_keeps_fact_and_falls_back_to_scan(self) -> None:
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
                maturity_task_id="maturity-task-fallback",
            )

            with patch(
                "stratgauge.data_pipeline.selection_store.SelectionReadIndex.upsert_outcome",
                side_effect=SelectionIndexError("simulated projection failure"),
            ):
                written = store.write_outcome(outcome)

            self.assertEqual(written, outcome)
            self.assertEqual(
                store.find_outcome_for_task("maturity-task-fallback", outcome.candidate_id),
                outcome,
            )
            self.assertEqual(len(tuple((Path(directory) / "tdx" / "outcomes").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
