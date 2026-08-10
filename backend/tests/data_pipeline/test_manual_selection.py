import json
import unittest
from datetime import UTC, datetime

from stratgauge.data_pipeline.manual_selection import (
    ManualSelectionCandidateInput,
    ManualSelectionError,
    build_manual_selection,
)
from stratgauge.domain.models import Exchange

RECORDED_AT = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 1, 5, 15, 10, tzinfo=UTC)


class ManualSelectionBuildTests(unittest.TestCase):
    def test_builds_canonical_evidence_and_manual_domain_models(self) -> None:
        result = build_manual_selection(
            selection_date=RECORDED_AT.date(),
            recorded_at=RECORDED_AT,
            imported_at=IMPORTED_AT,
            formula_id="formula-0123456789abcdef0123",
            formula_name="趋势选股",
            formula_version="v1",
            candidates=(
                ManualSelectionCandidateInput(
                    local_code=" 000001 ",
                    exchange=Exchange.SZ,
                    name=" 平安银行 ",
                    note=" 收盘后确认 ",
                ),
            ),
        )

        expected = {
            "candidates": [
                {
                    "exchange": "SZ",
                    "local_code": "000001",
                    "name": "平安银行",
                    "note": "收盘后确认",
                }
            ],
            "formula_id": "formula-0123456789abcdef0123",
            "recorded_at": "2026-01-05T15:05:00+00:00",
            "selection_date": "2026-01-05",
        }
        self.assertEqual(
            result.raw_content,
            json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
        )
        self.assertEqual(result.imported.batch.source, "manual")
        self.assertEqual(result.imported.batch.source_filename, "manual-2026-01-05.json")
        self.assertEqual(result.imported.batch.candidate_count, 1)
        self.assertEqual(result.imported.candidates[0].symbol, "000001.SZ")
        self.assertEqual(result.imported.candidates[0].source_fields, {"note": "收盘后确认"})

    def test_rejects_empty_candidate_list(self) -> None:
        with self.assertRaisesRegex(ManualSelectionError, "at least one candidate"):
            build_manual_selection(
                selection_date=RECORDED_AT.date(),
                recorded_at=RECORDED_AT,
                imported_at=IMPORTED_AT,
                formula_id="formula-0123456789abcdef0123",
                formula_name="公式 A",
                formula_version="v1",
                candidates=(),
            )

    def test_rejects_duplicate_symbols_without_guessing_exchange(self) -> None:
        with self.assertRaisesRegex(ManualSelectionError, "duplicate candidate symbol"):
            build_manual_selection(
                selection_date=RECORDED_AT.date(),
                recorded_at=RECORDED_AT,
                imported_at=IMPORTED_AT,
                formula_id="formula-0123456789abcdef0123",
                formula_name="公式 A",
                formula_version="v1",
                candidates=(
                    ManualSelectionCandidateInput("000001", Exchange.SZ),
                    ManualSelectionCandidateInput("000001", Exchange.SZ),
                ),
            )

    def test_same_local_code_can_be_recorded_for_distinct_explicit_exchanges(self) -> None:
        result = build_manual_selection(
            selection_date=RECORDED_AT.date(),
            recorded_at=RECORDED_AT,
            imported_at=IMPORTED_AT,
            formula_id="formula-0123456789abcdef0123",
            formula_name="公式 A",
            formula_version="v1",
            candidates=(
                ManualSelectionCandidateInput("000001", Exchange.SH),
                ManualSelectionCandidateInput("000001", Exchange.SZ),
            ),
        )

        self.assertEqual(
            [candidate.symbol for candidate in result.imported.candidates],
            ["000001.SH", "000001.SZ"],
        )


if __name__ == "__main__":
    unittest.main()
