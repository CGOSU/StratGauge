import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from stratgauge.application.ml import (
    BuildTrainingFeatureSnapshotsRequest,
    run_build_training_feature_snapshots,
)
from stratgauge.data_pipeline.ingest import ingest
from stratgauge.data_pipeline.quality import check_bars, quality_run_id
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStatus, SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_sources.base import DailyBarRequest, IndexDailyRequest, MarketDailyRequest
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security
from stratgauge.domain.selections import SelectionCandidate, TdxSelectionImport
from stratgauge.features.contract import (
    FeatureDefinition,
    FeatureSetVersion,
    feature_definition_sha256,
)
from stratgauge.features.store import FeatureSnapshotStore
from stratgauge.ml.training_datasets import (
    SelectionTrainingLabelRow,
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_observations import (
    TrainingFeatureSnapshotError,
    read_training_feature_snapshot_report,
)

SELECTION_DATE = date(2026, 2, 1)
EXPORTED_AT = datetime(2026, 2, 1, 9, tzinfo=UTC)
OBSERVATION_AT = datetime(2026, 2, 1, 10, tzinfo=UTC)


class TrainingFeatureObservationTests(unittest.TestCase):
    def test_builds_all_four_purposes_with_their_point_in_time_as_of(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as market_directory,
            TemporaryDirectory() as feature_directory,
            TemporaryDirectory() as report_directory,
        ):
            selection_store = SelectionImportStore(selection_directory)
            imported = _write_selection_batch(selection_store)
            market_store = SnapshotStore(market_directory)
            market_snapshot_ids = _write_market_history(market_store)
            feature_set = _close_feature_set()

            rows = (
                _label_row(
                    purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                    candidate=imported.candidates[0],
                    observation_date=SELECTION_DATE,
                    label_available_at=datetime(2026, 2, 20, 12, tzinfo=UTC),
                    target=TrainingLabel.SUCCESS,
                    source_hash="a" * 64,
                    tracking_policy_id="tracking-v1",
                    outcome_policy_id="outcome-v1",
                ),
                _label_row(
                    purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
                    candidate=imported.candidates[0],
                    observation_date=SELECTION_DATE,
                    observation_at=OBSERVATION_AT,
                    label_available_at=datetime(2026, 2, 2, 12, tzinfo=UTC),
                    target=TrainingLabel.WATCH,
                    source_hash="b" * 64,
                ),
                _label_row(
                    purpose=TrainingDatasetPurpose.BUY_TIMING,
                    candidate=imported.candidates[0],
                    observation_date=SELECTION_DATE,
                    observation_at=OBSERVATION_AT,
                    label_available_at=datetime(2026, 2, 3, 12, tzinfo=UTC),
                    target=TrainingLabel.NOT_PURCHASED,
                    source_hash="c" * 64,
                    window_sessions=3,
                ),
                _label_row(
                    purpose=TrainingDatasetPurpose.SELL_TIMING,
                    candidate=imported.candidates[0],
                    observation_date=date(2026, 1, 31),
                    observation_at=datetime(2026, 1, 31, 17, tzinfo=UTC),
                    label_available_at=datetime(2026, 2, 3, 12, tzinfo=UTC),
                    target=TrainingLabel.NOT_SOLD,
                    source_hash="d" * 64,
                    window_sessions=3,
                ),
            )

            for row in rows:
                report_path = Path(report_directory) / f"{row.purpose.value}.json"
                result = run_build_training_feature_snapshots(
                    BuildTrainingFeatureSnapshotsRequest(
                        labels=(row,),
                        selection_root=Path(selection_directory),
                        market_root=Path(market_directory),
                        feature_root=Path(feature_directory),
                        report_path=report_path,
                        market_snapshot_ids=market_snapshot_ids,
                        feature_set=feature_set,
                        benchmark_symbol=None,
                    )
                )

                self.assertEqual(result.report.snapshot_count, 1)
                self.assertEqual(result.report.failure_count, 0)
                mapping = result.report.mappings[0]
                snapshot = FeatureSnapshotStore(feature_directory).load(mapping.feature_snapshot_id)
                self.assertEqual(snapshot.selection_date, row.observation_date)
                self.assertLess(snapshot.input_session_end, row.observation_date)
                if row.observation_at is None:
                    self.assertEqual(mapping.as_of, EXPORTED_AT)
                    self.assertEqual(mapping.as_of_source, "batch_exported_at")
                else:
                    self.assertEqual(mapping.as_of, row.observation_at)
                    self.assertEqual(mapping.as_of_source, "observation_at")
                self.assertEqual(snapshot.as_of, mapping.as_of)
                self.assertEqual(read_training_feature_snapshot_report(report_path), result.report)

    def test_row_failure_is_recorded_for_future_or_unavailable_market_data(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as market_directory,
            TemporaryDirectory() as feature_directory,
            TemporaryDirectory() as report_directory,
        ):
            selection_store = SelectionImportStore(selection_directory)
            imported = _write_selection_batch(selection_store)
            market_store = SnapshotStore(market_directory)
            market_snapshot_ids = _write_market_history(
                market_store,
                available_at=datetime(2026, 2, 2, 16, tzinfo=UTC),
            )
            row = _label_row(
                purpose=TrainingDatasetPurpose.ANNOTATION_DECISION,
                candidate=imported.candidates[0],
                observation_date=SELECTION_DATE,
                observation_at=OBSERVATION_AT,
                label_available_at=datetime(2026, 2, 3, 12, tzinfo=UTC),
                target=TrainingLabel.WATCH,
                source_hash="e" * 64,
            )

            result = run_build_training_feature_snapshots(
                BuildTrainingFeatureSnapshotsRequest(
                    labels=(row,),
                    selection_root=Path(selection_directory),
                    market_root=Path(market_directory),
                    feature_root=Path(feature_directory),
                    report_path=Path(report_directory) / "report.json",
                    market_snapshot_ids=market_snapshot_ids,
                    feature_set=_close_feature_set(),
                    benchmark_symbol=None,
                )
            )

            self.assertEqual(result.report.snapshot_count, 0)
            self.assertEqual(result.report.failure_count, 1)
            self.assertIn("not available at as_of", result.report.failures[0].reason)
            self.assertEqual(FeatureSnapshotStore(feature_directory).list_snapshots(), ())

    def test_default_feature_set_requires_explicit_benchmark_evidence(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as market_directory:
            selection_store = SelectionImportStore(selection_directory)
            imported = _write_selection_batch(selection_store)
            market_store = SnapshotStore(market_directory)
            market_snapshot_ids = _write_market_history(market_store)
            row = _label_row(
                purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                candidate=imported.candidates[0],
                observation_date=SELECTION_DATE,
                label_available_at=datetime(2026, 2, 20, 12, tzinfo=UTC),
                target=TrainingLabel.SUCCESS,
                source_hash="f" * 64,
                tracking_policy_id="tracking-v1",
                outcome_policy_id="outcome-v1",
            )

            with self.assertRaisesRegex(TrainingFeatureSnapshotError, "benchmark_snapshot_ids"):
                run_build_training_feature_snapshots(
                    BuildTrainingFeatureSnapshotsRequest(
                        labels=(row,),
                        selection_root=Path(selection_directory),
                        market_root=Path(market_directory),
                        feature_root=Path(market_directory) / "features",
                        report_path=Path(market_directory) / "report.json",
                        market_snapshot_ids=market_snapshot_ids,
                    )
                )

    def test_wrong_request_kind_is_rejected_before_rows_are_processed(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as market_directory:
            selection_store = SelectionImportStore(selection_directory)
            imported = _write_selection_batch(selection_store)
            index_snapshot_id = _write_index_snapshot(SnapshotStore(market_directory))
            row = _label_row(
                purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                candidate=imported.candidates[0],
                observation_date=SELECTION_DATE,
                label_available_at=datetime(2026, 2, 20, 12, tzinfo=UTC),
                target=TrainingLabel.SUCCESS,
                source_hash="1" * 64,
                tracking_policy_id="tracking-v1",
                outcome_policy_id="outcome-v1",
            )

            with self.assertRaisesRegex(TrainingFeatureSnapshotError, "request_kind"):
                run_build_training_feature_snapshots(
                    BuildTrainingFeatureSnapshotsRequest(
                        labels=(row,),
                        selection_root=Path(selection_directory),
                        market_root=Path(market_directory),
                        feature_root=Path(market_directory) / "features",
                        report_path=Path(market_directory) / "report.json",
                        market_snapshot_ids=(index_snapshot_id,),
                        feature_set=_close_feature_set(),
                        benchmark_symbol=None,
                    )
                )

    def test_report_replay_requires_byte_identical_content(self) -> None:
        with (
            TemporaryDirectory() as selection_directory,
            TemporaryDirectory() as market_directory,
            TemporaryDirectory() as feature_directory,
            TemporaryDirectory() as report_directory,
        ):
            selection_store = SelectionImportStore(selection_directory)
            imported = _write_selection_batch(selection_store)
            market_store = SnapshotStore(market_directory)
            market_snapshot_ids = _write_market_history(market_store)
            request = BuildTrainingFeatureSnapshotsRequest(
                labels=(
                    _label_row(
                        purpose=TrainingDatasetPurpose.CANDIDATE_SUCCESS,
                        candidate=imported.candidates[0],
                        observation_date=SELECTION_DATE,
                        label_available_at=datetime(2026, 2, 20, 12, tzinfo=UTC),
                        target=TrainingLabel.SUCCESS,
                        source_hash="2" * 64,
                        tracking_policy_id="tracking-v1",
                        outcome_policy_id="outcome-v1",
                    ),
                ),
                selection_root=Path(selection_directory),
                market_root=Path(market_directory),
                feature_root=Path(feature_directory),
                report_path=Path(report_directory) / "report.json",
                market_snapshot_ids=market_snapshot_ids,
                feature_set=_close_feature_set(),
                benchmark_symbol=None,
            )
            first = run_build_training_feature_snapshots(request)
            self.assertEqual(run_build_training_feature_snapshots(request).report, first.report)
            request.report_path.write_text("different\n", encoding="utf-8", newline="\n")
            with self.assertRaises(TrainingFeatureSnapshotError):
                run_build_training_feature_snapshots(request)


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


def _write_market_history(
    store: SnapshotStore,
    *,
    available_at: datetime = datetime(2026, 1, 31, 16, tzinfo=UTC),
) -> tuple[str, ...]:
    security = Security(
        security_id="security-000001.SZ",
        local_code="000001",
        symbol="000001.SZ",
        exchange=Exchange.SZ,
    )
    snapshot_ids: list[str] = []
    for index in range(31):
        session = date(2026, 1, 1) + timedelta(days=index)
        request = MarketDailyRequest(
            session_date=session,
            universe_snapshot_id="universe-fixture",
            expected_security_ids=(security.security_id,),
        )

        def fetch_market(
            _request: MarketDailyRequest,
            session: date = session,
            symbol: str = security.symbol,
        ) -> list[dict[str, str]]:
            return [_market_row(session, symbol)]

        def clock() -> datetime:
            return available_at

        adapter = TushareAdapter(market_fetcher=fetch_market, clock=clock)
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


def _write_index_snapshot(store: SnapshotStore) -> str:
    security = Security(
        security_id="index-000300.SH",
        local_code="000300",
        symbol="000300.SH",
        exchange=Exchange.SH,
    )
    request = IndexDailyRequest(
        security=security,
        start_date=date(2026, 1, 31),
        end_date=date(2026, 1, 31),
    )

    def fetch_index(_request: DailyBarRequest) -> list[dict[str, str]]:
        return [_index_row(date(2026, 1, 31))]

    def clock() -> datetime:
        return datetime(2026, 2, 1, 17, tzinfo=UTC)

    result = ingest(
        TushareAdapter(fetcher=fetch_index, clock=clock),
        request,
        store,
        expected_sessions=(date(2026, 1, 31),),
        expected_security_ids=(security.security_id,),
    )
    return result.manifest.snapshot_id


def _close_feature_set() -> FeatureSetVersion:
    definitions = (
        FeatureDefinition(
            name="close",
            description="fixture close",
            lookback_sessions=1,
            unit="price",
        ),
    )
    return FeatureSetVersion(
        feature_set_version_id="features-observations-fixture-v1",
        name="observations-fixture",
        definitions=definitions,
        definition_sha256=feature_definition_sha256(definitions),
    )


def _label_row(
    *,
    purpose: TrainingDatasetPurpose,
    candidate: SelectionCandidate,
    observation_date: date,
    label_available_at: datetime,
    target: TrainingLabel,
    source_hash: str,
    observation_at: datetime | None = None,
    tracking_policy_id: str | None = None,
    outcome_policy_id: str | None = None,
    window_sessions: int | None = None,
) -> SelectionTrainingLabelRow:
    return SelectionTrainingLabelRow(
        purpose=purpose,
        label_contract_id=label_contract_id_for(purpose),
        sample_id=f"sample-{source_hash[:8]}",
        source_schema_version="fixture-v1",
        source_row_sha256=source_hash,
        source_reference_id=f"fixture:{source_hash[:8]}",
        candidate_id=candidate.candidate_id,
        batch_id=candidate.batch_id,
        symbol=candidate.symbol,
        observation_date=observation_date,
        observation_at=observation_at,
        label_available_at=label_available_at,
        target=target,
        tracking_policy_id=tracking_policy_id,
        outcome_policy_id=outcome_policy_id,
        window_sessions=window_sessions,
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
