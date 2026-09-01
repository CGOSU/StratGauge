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
from unittest.mock import Mock, patch

from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.selections import SelectionLifecycleStatus, TdxSelectionImport


class SelectionMLMarketEvidenceCliTests(unittest.TestCase):
    def test_cli_resolves_archived_scope_and_does_not_start_followup_research(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as universe_directory,
            TemporaryDirectory() as market_directory,
            TemporaryDirectory() as output_directory,
        ):
            selection_root = Path(selection_directory)
            store = SelectionImportStore(selection_root)
            later = _write_batch(store, date(2026, 1, 7), "000002")
            earlier = _write_batch(store, date(2026, 1, 5), "000001")
            store.set_batch_status(
                earlier.batch.batch_id,
                SelectionLifecycleStatus.ARCHIVED,
                changed_at=datetime(2026, 1, 8, tzinfo=UTC),
                reason="fixture archive",
            )
            fake_report = Mock()
            fake_report.ready = True
            fake_report.model_dump_json.return_value = json.dumps(
                {"report_version": "fixture", "ready": True}
            )
            main = _load_main_with_service(fake_report)
            output = StringIO()
            arguments = [
                "prepare_selection_ml_market_evidence.py",
                "--all-batches",
                "--selection-root",
                str(selection_root),
                "--universe-root",
                universe_directory,
                "--universe-snapshot-id",
                "universe-fixture",
                "--market-root",
                market_directory,
                "--calendar-cache",
                str(Path(output_directory) / "calendar.txt"),
                "--reference-root",
                output_directory,
                "--lookback-sessions",
                "7",
                "--maturity-horizon-sessions",
                "4",
                "--now",
                "2026-01-31T12:00:00+00:00",
            ]
            with (
                patch.object(sys, "argv", arguments),
                redirect_stdout(output),
            ):
                exit_code = main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue())["ready"], True)
            service = cast(Mock, main.__globals__["prepare_selection_ml_market_evidence"])
            service.assert_called_once()
            request = service.call_args.kwargs["request"]
            self.assertEqual(request.batch_ids, (earlier.batch.batch_id, later.batch.batch_id))
            self.assertEqual(request.lookback_sessions, 7)
            self.assertEqual(request.maturity_horizon_sessions, 4)
            self.assertEqual(
                service.call_args.kwargs["calendar_path"], Path(output_directory) / "calendar.txt"
            )


def _load_main_with_service(report: Mock) -> Callable[[], int]:
    script = Path(__file__).parents[2] / "scripts" / "prepare_selection_ml_market_evidence.py"
    with patch(
        "stratgauge.application.ml_market_evidence.prepare_selection_ml_market_evidence",
        return_value=report,
    ):
        globals_ = runpy.run_path(str(script), run_name="prepare_selection_ml_market_evidence")
    return cast(Callable[[], int], globals_["main"])


def _write_batch(
    store: SelectionImportStore,
    selection_date: date,
    code: str,
) -> TdxSelectionImport:
    content = f"code,name,market\n{code},Fixture,SZ\n".encode()
    exported_at = datetime.combine(selection_date, datetime.min.time(), tzinfo=UTC).replace(
        hour=15,
        minute=5,
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
