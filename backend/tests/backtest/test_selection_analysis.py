import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.backtest.selection_analysis import analyze_selection_candidate
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.selections import (
    SelectionOutcome,
    SelectionOutcomeStatus,
    TdxSelectionImport,
)

CONTENT = "代码,名称,市场\n000001,平安银行,SZ\n".encode()
FORMULA_ID = "formula-" + "a" * 20


class SelectionAnalysisTests(unittest.TestCase):
    def test_analysis_uses_only_prior_visible_same_formula_outcomes(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            current = _import(date(2026, 1, 5), datetime(2026, 1, 5, 15, tzinfo=UTC))
            store.write(CONTENT, current)
            first = _import(date(2026, 1, 1), datetime(2026, 1, 1, 15, tzinfo=UTC))
            second = _import(date(2026, 1, 2), datetime(2026, 1, 2, 15, tzinfo=UTC))
            store.write(CONTENT, first)
            store.write(CONTENT, second)
            store.write_outcome(_outcome(first, Decimal("10"), SelectionOutcomeStatus.WIN))
            store.write_outcome(_outcome(second, Decimal("12"), SelectionOutcomeStatus.LOSS))
            future = _outcome(first, Decimal("99"), SelectionOutcomeStatus.WIN).model_copy(
                update={"evaluated_at": datetime(2026, 1, 6, 15, tzinfo=UTC)}
            )
            store.write_outcome(future)

            analysis = analyze_selection_candidate(
                store,
                batch_id=current.batch.batch_id,
                candidate_id=current.candidates[0].candidate_id,
                minimum_evidence_samples=2,
            )

            self.assertEqual(analysis.tracking_policy_id, "policy-v1")
            self.assertEqual(analysis.conditional_stats.completed, 2)
            self.assertTrue(analysis.conditional_stats.evidence_sufficient)
            self.assertEqual(analysis.conditional_stats.win_rate, Decimal("0.5"))
            self.assertEqual(analysis.entry_price_stats.sample_count, 2)
            self.assertEqual(analysis.entry_price_stats.minimum, Decimal("10"))
            self.assertEqual(analysis.entry_price_stats.maximum, Decimal("12"))
            self.assertEqual(analysis.entry_price_stats.median, Decimal("11"))
            self.assertEqual(analysis.cohort_batch_count, 2)


def _import(selection_date: date, exported_at: datetime) -> TdxSelectionImport:
    return import_tdx_export(
        CONTENT,
        selection_date=selection_date,
        exported_at=exported_at,
        imported_at=exported_at,
        formula_name="趋势公式",
        formula_version="v1",
        formula_id=FORMULA_ID,
    )


def _outcome(
    imported: TdxSelectionImport,
    entry_price: Decimal,
    status: SelectionOutcomeStatus,
) -> SelectionOutcome:
    return SelectionOutcome(
        candidate_id=imported.candidates[0].candidate_id,
        batch_id=imported.batch.batch_id,
        selection_date=imported.batch.selection_date,
        status=status,
        evaluated_at=datetime(2026, 1, 4, 15, tzinfo=UTC),
        horizon_sessions=1,
        entry_session=date(2026, 1, 3),
        exit_session=date(2026, 1, 4),
        entry_price=entry_price,
        exit_price=entry_price + Decimal("1")
        if status is SelectionOutcomeStatus.WIN
        else entry_price,
        gross_return=Decimal("0.1") if status is SelectionOutcomeStatus.WIN else Decimal("-0.1"),
        net_return=Decimal("0.1") if status is SelectionOutcomeStatus.WIN else Decimal("-0.1"),
        reason="fixture",
        tracking_policy_id="policy-v1",
        outcome_policy_id="outcome-v1",
        cost_schedule_id="cost-v1",
        position_notional=Decimal("10000"),
    )


if __name__ == "__main__":
    unittest.main()
