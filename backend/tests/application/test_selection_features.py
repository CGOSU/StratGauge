import unittest
from datetime import UTC, date, datetime, timedelta
from tempfile import TemporaryDirectory

from stratgauge.application.selection_features import build_selection_feature_snapshots
from stratgauge.data_pipeline.market_daily_sync import sync_market_session
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.store import SnapshotStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.data_pipeline.universe import build_universe_snapshot
from stratgauge.data_sources.tushare import TushareAdapter
from stratgauge.domain.models import Exchange, Security
from stratgauge.features.contract import (
    FeatureDefinition,
    FeatureSetVersion,
    feature_definition_sha256,
)
from stratgauge.features.store import FeatureSnapshotStore

CONTENT = "代码,名称,市场\n000001,平安银行,SZ\n000002,万科A,SZ\n".encode()
SELECTION_DATE = date(2026, 2, 1)
AS_OF = datetime(2026, 2, 1, 16, tzinfo=UTC)


class SelectionFeatureTests(unittest.TestCase):
    def test_builds_candidate_features_from_approved_local_snapshots(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as market_directory:
            selection_store = SelectionImportStore(selection_directory)
            imported = import_tdx_export(
                CONTENT,
                selection_date=SELECTION_DATE,
                exported_at=datetime(2026, 2, 1, 9, tzinfo=UTC),
                imported_at=datetime(2026, 2, 1, 10, tzinfo=UTC),
                formula_name="公式 A",
            )
            selection_store.write(CONTENT, imported)
            securities = tuple(
                Security(
                    security_id=f"security-{code}",
                    local_code=code,
                    symbol=f"{code}.SZ",
                    exchange=Exchange.SZ,
                )
                for code in ("000001", "000002")
            )
            universe = build_universe_snapshot(
                as_of=datetime(2026, 1, 1, 17, tzinfo=UTC),
                source="fixture",
                source_snapshot_ids=("reference-1",),
                securities=securities,
            )
            market_store = SnapshotStore(market_directory)
            snapshots: list[str] = []
            adapter = TushareAdapter(
                market_fetcher=lambda request: [
                    _row(request.session_date, "000001.SZ"),
                    _row(request.session_date, "000002.SZ"),
                ],
                clock=lambda: datetime(2026, 2, 1, 15, tzinfo=UTC),
            )
            for session in (SELECTION_DATE - timedelta(days=2), SELECTION_DATE - timedelta(days=1)):
                result = sync_market_session(
                    adapter=adapter,
                    session_date=session,
                    universe=universe,
                    store=market_store,
                )
                self.assertEqual(result.status, "approved")
                snapshots.append(result.snapshot_id)

            definitions = (
                FeatureDefinition(
                    name="close",
                    description="收盘价",
                    lookback_sessions=1,
                    unit="price",
                ),
                FeatureDefinition(
                    name="return_1",
                    description="一日收益率",
                    lookback_sessions=1,
                    unit="ratio",
                ),
            )
            feature_set = FeatureSetVersion(
                feature_set_version_id="features-local-fixture-v1",
                name="local-fixture",
                definitions=definitions,
                definition_sha256=feature_definition_sha256(definitions),
            )
            report = build_selection_feature_snapshots(
                imported,
                market_store=market_store,
                feature_store=FeatureSnapshotStore(selection_directory),
                market_snapshot_ids=snapshots,
                as_of=AS_OF,
                feature_set=feature_set,
                benchmark_symbol=None,
            )

            self.assertEqual(len(report.candidate_snapshot_ids), 2)
            self.assertEqual(report.failures, ())
            self.assertEqual(report.feature_set_version_id, feature_set.feature_set_version_id)
            feature_store = FeatureSnapshotStore(selection_directory)
            for _, snapshot_id in report.candidate_snapshot_ids:
                snapshot = feature_store.load(snapshot_id)
                self.assertEqual(snapshot.source_snapshot_ids, tuple(snapshots))
                self.assertLess(snapshot.input_session_end, SELECTION_DATE)

    def test_candidate_failure_does_not_discard_other_candidates(self) -> None:
        with TemporaryDirectory() as selection_directory, TemporaryDirectory() as market_directory:
            selection_store = SelectionImportStore(selection_directory)
            imported = import_tdx_export(
                CONTENT,
                selection_date=SELECTION_DATE,
                exported_at=datetime(2026, 2, 1, 9, tzinfo=UTC),
                imported_at=datetime(2026, 2, 1, 10, tzinfo=UTC),
                formula_name="公式 A",
            )
            selection_store.write(CONTENT, imported)
            security = Security(
                security_id="security-000001",
                local_code="000001",
                symbol="000001.SZ",
                exchange=Exchange.SZ,
            )
            universe = build_universe_snapshot(
                as_of=datetime(2026, 1, 1, 17, tzinfo=UTC),
                source="fixture",
                source_snapshot_ids=("reference-1",),
                securities=(security,),
            )
            market_store = SnapshotStore(market_directory)
            result = sync_market_session(
                adapter=TushareAdapter(
                    market_fetcher=lambda request: [_row(request.session_date, "000001.SZ")],
                    clock=lambda: datetime(2026, 2, 1, 15, tzinfo=UTC),
                ),
                session_date=SELECTION_DATE - timedelta(days=1),
                universe=universe,
                store=market_store,
            )
            report = build_selection_feature_snapshots(
                imported,
                market_store=market_store,
                feature_store=FeatureSnapshotStore(selection_directory),
                market_snapshot_ids=(result.snapshot_id,),
                as_of=AS_OF,
                feature_set=_feature_set(),
                benchmark_symbol=None,
            )
            self.assertEqual(len(report.candidate_snapshot_ids), 1)
            self.assertEqual(len(report.failures), 1)
            self.assertTrue(
                any(failure.candidate_id.endswith(":000002.SZ") for failure in report.failures)
            )


def _feature_set() -> FeatureSetVersion:
    definitions = (
        FeatureDefinition(
            name="close",
            description="收盘价",
            lookback_sessions=1,
            unit="price",
        ),
    )
    return FeatureSetVersion(
        feature_set_version_id="features-local-fixture-v1",
        name="local-fixture",
        definitions=definitions,
        definition_sha256=feature_definition_sha256(definitions),
    )


def _row(session: date, symbol: str) -> dict[str, str]:
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


if __name__ == "__main__":
    unittest.main()
