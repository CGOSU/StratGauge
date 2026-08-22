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

from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.quality import check_bars, quality_run_id
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_sources.base import (
    DailyBarRequest,
    IndexDailyRequest,
    MarketDailyRequest,
)
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.selections import TdxSelectionImport
from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
    serialize_training_label_dataset,
)

SELECTION_DATE = date(2026, 2, 1)
EXPORTED_AT = datetime(2026, 2, 1, 9, tzinfo=UTC)


class TrainingFeatureSnapshotCliTests(unittest.TestCase):
    def test_cli_reads_prepared_splits_and_prints_immutable_report_identity(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as market_directory,
            TemporaryDirectory() as labels_directory,
            TemporaryDirectory() as output_directory,
        ):
            selection_root = Path(selection_directory)
            market_root = Path(market_directory)
            labels_root = Path(labels_directory)
            output_root = Path(output_directory)
            selection_store = SelectionImportStore(selection_root)
            imported = _write_selection_batch(selection_store)
            row = _label_row(imported)
            _write_label_splits(labels_root, row)
            market_snapshot_ids = _write_candidate_history(SnapshotStore(market_root))
            benchmark_snapshot_ids = _write_benchmark_history(SnapshotStore(market_root))

            main = _load_main()
            argv = _arguments(
                labels_root,
                selection_root,
                output_root,
                market_root,
                market_snapshot_ids,
                benchmark_snapshot_ids,
            )
            first_output = StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(first_output):
                first_exit_code = main()

            first_payload = json.loads(first_output.getvalue())
            self.assertEqual(first_exit_code, 0)
            self.assertTrue(
                first_payload["report_id"].startswith("training-feature-snapshot-report-")
            )
            self.assertEqual(first_payload["snapshot_count"], 1)
            self.assertEqual(first_payload["failure_count"], 0)
            self.assertEqual(len(first_payload["feature_snapshot_ids"]), 1)
            self.assertTrue((output_root / "report.json").is_file())

            second_output = StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(second_output):
                second_exit_code = main()
            self.assertEqual(second_exit_code, 0)
            self.assertEqual(json.loads(second_output.getvalue()), first_payload)

    def test_cli_returns_json_error_for_missing_prepared_split(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as market_directory,
            TemporaryDirectory() as labels_directory,
            TemporaryDirectory() as output_directory,
        ):
            labels_root = Path(labels_directory)
            for filename in ("train.jsonl", "validation.jsonl"):
                (labels_root / filename).write_text("", encoding="utf-8", newline="\n")
            main = _load_main()
            argv = [
                "build_selection_training_feature_snapshots.py",
                "--labels-dir",
                str(labels_root),
                "--selection-root",
                selection_directory,
                "--feature-root",
                str(Path(output_directory) / "features"),
                "--market-root",
                market_directory,
                "--market-snapshot-id",
                "missing-market",
                "--as-of",
                "2026-02-02T00:00:00+00:00",
                "--output-dir",
                output_directory,
            ]
            output = StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                exit_code = main()
            self.assertEqual(exit_code, 2)
            self.assertIn(
                "prepared label split does not exist", json.loads(output.getvalue())["error"]
            )


def _load_main() -> Callable[[], int]:
    script = Path(__file__).parents[2] / "scripts" / "build_selection_training_feature_snapshots.py"
    globals_ = runpy.run_path(str(script), run_name="build_selection_training_feature_snapshots")
    return cast(Callable[[], int], globals_["main"])


def _arguments(
    labels_root: Path,
    selection_root: Path,
    output_root: Path,
    market_root: Path,
    market_snapshot_ids: tuple[str, ...],
    benchmark_snapshot_ids: tuple[str, ...],
) -> list[str]:
    arguments = [
        "build_selection_training_feature_snapshots.py",
        "--labels-dir",
        str(labels_root),
        "--selection-root",
        str(selection_root),
        "--feature-root",
        str(output_root / "features"),
        "--market-root",
        str(market_root),
    ]
    for snapshot_id in market_snapshot_ids:
        arguments.extend(("--market-snapshot-id", snapshot_id))
    for snapshot_id in benchmark_snapshot_ids:
        arguments.extend(("--benchmark-snapshot-id", snapshot_id))
    arguments.extend(
        (
            "--as-of",
            "2026-02-02T00:00:00+00:00",
            "--output-dir",
            str(output_root),
        )
    )
    return arguments


def _write_label_splits(labels_root: Path, row: SelectionTrainingLabelRow) -> None:
    labels_root.mkdir(parents=True, exist_ok=True)
    (labels_root / "train.jsonl").write_text(
        serialize_training_label_dataset((row,)), encoding="utf-8", newline="\n"
    )
    for filename in ("validation.jsonl", "test.jsonl"):
        (labels_root / filename).write_text("", encoding="utf-8", newline="\n")


def _write_selection_batch(store: SelectionImportStore) -> TdxSelectionImport:
    content = "代码,名称,市场\n000001,平安银行,SZ\n"
    imported = import_tdx_export(
        content.encode("utf-8"),
        selection_date=SELECTION_DATE,
        exported_at=EXPORTED_AT,
        imported_at=datetime(2026, 2, 1, 10, tzinfo=UTC),
        formula_name="fixture",
    )
    store.write(content.encode("utf-8"), imported)
    return imported


def _label_row(imported: TdxSelectionImport) -> SelectionTrainingLabelRow:
    candidate = imported.candidates[0]
    purpose = TrainingDatasetPurpose.CANDIDATE_SUCCESS
    return SelectionTrainingLabelRow(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        sample_id="sample-cli-fixture",
        source_schema_version="fixture-v1",
        source_row_sha256="a" * 64,
        source_reference_id="fixture:cli",
        candidate_id=candidate.candidate_id,
        batch_id=candidate.batch_id,
        symbol=candidate.symbol,
        observation_date=SELECTION_DATE,
        label_available_at=datetime(2026, 2, 20, 12, tzinfo=UTC),
        target=TrainingLabel.SUCCESS,
        tracking_policy_id="tracking-v1",
        outcome_policy_id="outcome-v1",
    )


def _write_candidate_history(store: SnapshotStore) -> tuple[str, ...]:
    security = Security(
        security_id="security-000001.SZ",
        local_code="000001",
        symbol="000001.SZ",
        exchange=Exchange.SZ,
    )
    return _write_daily_snapshots(
        store,
        security,
        lambda session: _market_row(session, security.symbol),
    )


def _write_benchmark_history(store: SnapshotStore) -> tuple[str, ...]:
    security = Security(
        security_id="index-000300.SH",
        local_code="000300",
        symbol="000300.SH",
        exchange=Exchange.SH,
    )
    snapshot_ids: list[str] = []
    for index in range(31):
        session = date(2026, 1, 1) + timedelta(days=index)
        request = IndexDailyRequest(
            security=security,
            start_date=session,
            end_date=session,
        )

        def fetch_index(_request: DailyBarRequest, session: date = session) -> list[dict[str, str]]:
            return [_index_row(session)]

        result = ingest(
            TushareAdapter(
                fetcher=fetch_index,
                clock=lambda: datetime(2026, 1, 31, 16, tzinfo=UTC),
            ),
            request,
            store,
            expected_sessions=(session,),
            expected_security_ids=(security.security_id,),
        )
        snapshot_ids.append(result.manifest.snapshot_id)
    return tuple(snapshot_ids)


def _write_daily_snapshots(
    store: SnapshotStore,
    security: Security,
    row_builder: Callable[[date], dict[str, str]],
) -> tuple[str, ...]:
    snapshot_ids: list[str] = []
    for index in range(31):
        session = date(2026, 1, 1) + timedelta(days=index)
        request = _market_request(session, security)

        def fetch_market(
            _request: MarketDailyRequest,
            session: date = session,
        ) -> list[dict[str, str]]:
            return [row_builder(session)]

        adapter = TushareAdapter(
            market_fetcher=fetch_market,
            clock=lambda: datetime(2026, 1, 31, 16, tzinfo=UTC),
        )
        result = adapter.fetch_market_daily(request, (security,))
        manifest = store.write(result)
        quality = check_bars(
            result.bars,
            snapshot_id=manifest.snapshot_id,
            expected_sessions=(session,),
            expected_security_ids=(security.security_id,),
        )
        store.save_quality_report(quality, quality_run_id(quality))
        approved = store.update_status(
            manifest.snapshot_id,
            SnapshotStatus.APPROVED,
            quality_run_id(quality),
        )
        snapshot_ids.append(approved.snapshot_id)
    return tuple(snapshot_ids)


def _market_request(session: date, security: Security) -> MarketDailyRequest:
    return MarketDailyRequest(
        session_date=session,
        universe_snapshot_id="universe-cli-fixture",
        expected_security_ids=(security.security_id,),
    )


def _market_row(session: date, symbol: str) -> dict[str, str]:
    return {
        "trade_date": session.strftime("%Y%m%d"),
        "ts_code": symbol,
        "pre_close": "10.00",
        "open": "10.00",
        "high": "11.00",
        "low": "9.90",
        "close": "11.00",
        "vol": "100",
        "amount": "10",
    }


def _index_row(session: date) -> dict[str, str]:
    return {
        "trade_date": session.strftime("%Y%m%d"),
        "ts_code": "000300.SH",
        "pre_close": "4000.00",
        "open": "4000.00",
        "high": "4010.00",
        "low": "3990.00",
        "close": "4005.00",
        "vol": "100",
        "amount": "10",
    }


if __name__ == "__main__":
    unittest.main()
