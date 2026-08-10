import hashlib
import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from stratgauge.data_pipeline.tdx_import import TdxImportError, import_tdx_export
from stratgauge.domain.selections import ManualSelectionDecision, ManualSelectionLabel

EXPORTED_AT = datetime(2026, 1, 5, 15, 5, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 1, 5, 15, 10, tzinfo=UTC)


class TdxImportTests(unittest.TestCase):
    def test_imports_gb18030_tsv_and_preserves_source_fields(self) -> None:
        content = (
            "代码\t名称\t市场\t评分\n000001\t平安银行\t深圳\t85\n600000\t浦发银行\t上海\t82\n"
        ).encode("gb18030")

        imported = import_tdx_export(
            content,
            selection_date=EXPORTED_AT.date(),
            exported_at=EXPORTED_AT,
            imported_at=IMPORTED_AT,
            formula_name="趋势选股",
            formula_version="v3",
        )

        self.assertEqual(imported.batch.source, "tongdaxin")
        self.assertEqual(imported.batch.candidate_count, 2)
        self.assertEqual(
            imported.batch.source_file_sha256,
            hashlib.sha256(content).hexdigest(),
        )
        self.assertEqual(imported.candidates[0].symbol, "000001.SZ")
        self.assertEqual(imported.candidates[1].symbol, "600000.SH")
        self.assertEqual(imported.candidates[0].source_fields["评分"], "85")
        self.assertTrue(imported.candidates[0].candidate_id.startswith("tdx-"))

    def test_imports_tongdaxin_gbk_quote_table_without_exchange_column(self) -> None:
        content = (
            "代码\t名称(4)\t涨幅%\t现价\t\n"
            "002167\t东方锆业\t3.31\t17.79\t\n"
            "600593\t大连圣亚\t1.30\t39.81\t\n"
            "#数据来源:通达信\r\n"
        ).encode("gbk")

        imported = import_tdx_export(
            content,
            selection_date=EXPORTED_AT.date(),
            exported_at=EXPORTED_AT,
            imported_at=IMPORTED_AT,
            formula_name="公式 A",
        )

        self.assertEqual(
            [candidate.symbol for candidate in imported.candidates],
            ["002167.SZ", "600593.SH"],
        )
        self.assertEqual(imported.candidates[0].name, "东方锆业")
        self.assertEqual(imported.candidates[0].source_fields["涨幅%"], "3.31")

    def test_imports_tongdaxin_ebk_market_prefixed_codes(self) -> None:
        content = b"\r\n0002167\r\n1600593\r\n1603183\r\n1603980\r\n"

        imported = import_tdx_export(
            content,
            selection_date=EXPORTED_AT.date(),
            exported_at=EXPORTED_AT,
            imported_at=IMPORTED_AT,
            formula_name="公式 A",
        )

        self.assertEqual(
            [candidate.symbol for candidate in imported.candidates],
            ["002167.SZ", "600593.SH", "603183.SH", "603980.SH"],
        )
        self.assertEqual(imported.batch.candidate_count, 4)

    def test_accepts_explicit_symbol_suffix_without_exchange_column(self) -> None:
        content = "代码,名称\n000001.SZ,平安银行\n".encode()

        imported = import_tdx_export(
            content,
            selection_date=EXPORTED_AT.date(),
            exported_at=EXPORTED_AT,
            imported_at=IMPORTED_AT,
            formula_name="公式 A",
        )

        self.assertEqual(imported.candidates[0].exchange.value, "SZ")

    def test_rejects_code_only_rows_instead_of_guessing_exchange(self) -> None:
        content = "代码,名称\n000001,平安银行\n".encode()

        with self.assertRaisesRegex(TdxImportError, "no exchange"):
            import_tdx_export(
                content,
                selection_date=EXPORTED_AT.date(),
                exported_at=EXPORTED_AT,
                imported_at=IMPORTED_AT,
                formula_name="公式 A",
            )

    def test_rejects_duplicate_candidates(self) -> None:
        content = "代码,市场\n000001,SZ\n000001,SZ\n".encode()

        with self.assertRaisesRegex(TdxImportError, "duplicates candidate"):
            import_tdx_export(
                content,
                selection_date=EXPORTED_AT.date(),
                exported_at=EXPORTED_AT,
                imported_at=IMPORTED_AT,
                formula_name="公式 A",
            )


class ManualSelectionLabelTests(unittest.TestCase):
    def test_label_is_time_zoned_and_separate_from_outcome(self) -> None:
        label = ManualSelectionLabel(
            candidate_id="tdx-batch:000001.SZ",
            decision=ManualSelectionDecision.WATCH,
            labeled_at=IMPORTED_AT,
            note="等待下一交易日确认",
        )

        self.assertEqual(label.decision, ManualSelectionDecision.WATCH)
        self.assertEqual(label.note, "等待下一交易日确认")

    def test_label_rejects_naive_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            ManualSelectionLabel(
                candidate_id="candidate-1",
                decision=ManualSelectionDecision.PASS,
                labeled_at=datetime(2026, 1, 5, 15, 10),
            )


if __name__ == "__main__":
    unittest.main()
