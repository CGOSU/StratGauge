import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.selections import (
    ManualSelectionDecision,
    ManualSelectionLabel,
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


if __name__ == "__main__":
    unittest.main()
