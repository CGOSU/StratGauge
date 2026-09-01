import hashlib
import json
import runpy
import sys
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from stratgauge.backtest.selection_dataset import (
    read_selection_dataset,
    read_selection_ml_dataset_manifest,
)
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.selections import SelectionLifecycleStatus, TdxSelectionImport


class SelectionMLDatasetExportCliTests(unittest.TestCase):
    def test_all_batches_includes_archived_batches_and_writes_lineage_manifest(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as portfolio_directory,
            TemporaryDirectory() as output_directory,
        ):
            selection_root = Path(selection_directory)
            output_root = Path(output_directory)
            store = SelectionImportStore(selection_root)
            later = _write_batch(store, date(2026, 1, 7), "000002")
            earlier = _write_batch(store, date(2026, 1, 5), "000001")
            store.set_batch_status(
                earlier.batch.batch_id,
                SelectionLifecycleStatus.ARCHIVED,
                changed_at=datetime(2026, 1, 8, tzinfo=UTC),
                reason="fixture archive",
            )
            sessions_file = output_root / "sessions.txt"
            sessions_file.write_text("2026-01-05\n2026-01-06\n2026-01-07\n", encoding="utf-8")

            exit_code, payload = _run_cli(
                [
                    "--all-batches",
                    "--selection-root",
                    str(selection_root),
                    "--portfolio-root",
                    portfolio_directory,
                    "--sessions-file",
                    str(sessions_file),
                    "--evaluated-at",
                    "2026-01-08T17:00:00+00:00",
                    "--output-dir",
                    str(output_root / "bundle"),
                ]
            )

            bundle_root = output_root / "bundle"
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["selection_mode"], "all_batches")
            self.assertEqual(payload["batch_ids"], [earlier.batch.batch_id, later.batch.batch_id])
            rows = read_selection_dataset((bundle_root / "selection-dataset.jsonl").read_bytes())
            self.assertEqual(
                [row.batch_id for row in rows],
                [earlier.batch.batch_id, later.batch.batch_id],
            )
            self.assertEqual({row.source for row in rows}, {"tongdaxin"})
            manifest = read_selection_ml_dataset_manifest(
                (bundle_root / "manifest.json").read_bytes()
            )
            self.assertEqual(manifest.batch_ids, (earlier.batch.batch_id, later.batch.batch_id))
            self.assertTrue(manifest.include_archived_batches)
            self.assertEqual(
                tuple(item.dataset for item in manifest.files),
                ("selection", "annotation", "buy_timing", "sell_timing"),
            )
            for item in manifest.files:
                content = (bundle_root / item.path).read_bytes()
                row_count = len([line for line in content.splitlines() if line])
                self.assertEqual(item.row_count, row_count)
                self.assertEqual(item.sha256, hashlib.sha256(content).hexdigest())

    def test_single_batch_keeps_existing_output_contract_without_manifest(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as portfolio_directory,
            TemporaryDirectory() as output_directory,
        ):
            selection_root = Path(selection_directory)
            output_root = Path(output_directory)
            store = SelectionImportStore(selection_root)
            imported = _write_batch(store, date(2026, 1, 5), "000001")
            sessions_file = output_root / "sessions.txt"
            sessions_file.write_text("2026-01-05\n2026-01-06\n", encoding="utf-8")
            bundle_root = output_root / "bundle"

            exit_code, payload = _run_cli(
                [
                    "--batch-id",
                    imported.batch.batch_id,
                    "--selection-root",
                    str(selection_root),
                    "--portfolio-root",
                    portfolio_directory,
                    "--sessions-file",
                    str(sessions_file),
                    "--evaluated-at",
                    "2026-01-08T17:00:00+00:00",
                    "--output-dir",
                    str(bundle_root),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["batch_id"], imported.batch.batch_id)
            self.assertNotIn("selection_mode", payload)
            self.assertTrue((bundle_root / "selection-dataset.jsonl").exists())
            self.assertFalse((bundle_root / "manifest.json").exists())

    def test_date_range_is_inclusive_and_rejects_duplicate_batch_ids_without_outputs(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as portfolio_directory,
            TemporaryDirectory() as output_directory,
        ):
            selection_root = Path(selection_directory)
            output_root = Path(output_directory)
            store = SelectionImportStore(selection_root)
            included = _write_batch(store, date(2026, 1, 5), "000001")
            _write_batch(store, date(2026, 1, 7), "000002")
            sessions_file = output_root / "sessions.txt"
            sessions_file.write_text("2026-01-05\n2026-01-06\n2026-01-07\n", encoding="utf-8")

            range_output = output_root / "range"
            exit_code, payload = _run_cli(
                [
                    "--start-date",
                    "2026-01-05",
                    "--end-date",
                    "2026-01-05",
                    "--selection-root",
                    str(selection_root),
                    "--portfolio-root",
                    portfolio_directory,
                    "--sessions-file",
                    str(sessions_file),
                    "--evaluated-at",
                    "2026-01-08T17:00:00+00:00",
                    "--output-dir",
                    str(range_output),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["batch_ids"], [included.batch.batch_id])
            manifest = read_selection_ml_dataset_manifest(
                (range_output / "manifest.json").read_bytes()
            )
            self.assertEqual(manifest.selection_date_from, date(2026, 1, 5))
            self.assertEqual(manifest.selection_date_to, date(2026, 1, 5))
            self.assertEqual(manifest.requested_selection_date_from, date(2026, 1, 5))
            self.assertEqual(manifest.requested_selection_date_to, date(2026, 1, 5))

            duplicate_output = output_root / "duplicate"
            exit_code, payload = _run_cli(
                [
                    "--batch-id",
                    included.batch.batch_id,
                    "--batch-id",
                    included.batch.batch_id,
                    "--selection-root",
                    str(selection_root),
                    "--portfolio-root",
                    portfolio_directory,
                    "--sessions-file",
                    str(sessions_file),
                    "--evaluated-at",
                    "2026-01-08T17:00:00+00:00",
                    "--output-dir",
                    str(duplicate_output),
                ]
            )
            self.assertEqual(exit_code, 2)
            self.assertIn("unique", str(payload["error"]))
            self.assertFalse(duplicate_output.exists())

    def test_existing_target_fails_before_creating_other_bundle_files(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as portfolio_directory,
            TemporaryDirectory() as output_directory,
        ):
            selection_root = Path(selection_directory)
            output_root = Path(output_directory)
            store = SelectionImportStore(selection_root)
            _write_batch(store, date(2026, 1, 5), "000001")
            sessions_file = output_root / "sessions.txt"
            sessions_file.write_text("2026-01-05\n2026-01-06\n", encoding="utf-8")
            bundle_root = output_root / "bundle"
            bundle_root.mkdir()
            existing = bundle_root / "selection-dataset.jsonl"
            existing.write_text("existing\n", encoding="utf-8")

            exit_code, payload = _run_cli(
                [
                    "--all-batches",
                    "--selection-root",
                    str(selection_root),
                    "--portfolio-root",
                    portfolio_directory,
                    "--sessions-file",
                    str(sessions_file),
                    "--evaluated-at",
                    "2026-01-08T17:00:00+00:00",
                    "--output-dir",
                    str(bundle_root),
                ]
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["error"], "dataset output already exists")
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing\n")
            self.assertFalse((bundle_root / "manifest.json").exists())
            self.assertFalse((bundle_root / "annotation-dataset.jsonl").exists())


def _load_main() -> Callable[[], int]:
    script = Path(__file__).parents[2] / "scripts" / "export_selection_ml_datasets.py"
    globals_ = runpy.run_path(str(script), run_name="export_selection_ml_datasets")
    return cast(Callable[[], int], globals_["main"])


def _run_cli(arguments: list[str]) -> tuple[int, dict[str, object]]:
    output = StringIO()
    main = _load_main()
    with (
        patch.object(sys, "argv", ["export_selection_ml_datasets.py", *arguments]),
        redirect_stdout(output),
    ):
        exit_code = main()
    return exit_code, cast(dict[str, object], json.loads(output.getvalue()))


def _write_batch(
    store: SelectionImportStore,
    selection_date: date,
    code: str,
) -> TdxSelectionImport:
    content = f"代码,名称,市场\n{code},Fixture,SZ\n".encode()
    exported_at = datetime.combine(selection_date, datetime.min.time(), tzinfo=UTC).replace(
        hour=15, minute=5
    )
    imported = import_tdx_export(
        content,
        selection_date=selection_date,
        exported_at=exported_at,
        imported_at=exported_at + timedelta(minutes=5),
        formula_name="fixture",
    )
    store.write(content, imported)
    return imported


if __name__ == "__main__":
    unittest.main()
