import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.domain.models import DailyBar
from stratgauge.features.contract import default_feature_set
from stratgauge.features.engine import FeatureComputationError, compute_daily_features
from stratgauge.features.store import FeatureSnapshotStore

SELECTION_DATE = date(2026, 2, 1)
AS_OF = datetime(2026, 2, 1, 16, tzinfo=UTC)


class FeatureEngineTests(unittest.TestCase):
    def test_default_feature_set_is_deterministic_and_computes_lineage(self) -> None:
        candidate = _bars("security-candidate", "000001.SZ", Decimal("10"))
        benchmark = _bars(
            "security-benchmark",
            "000300.SH",
            Decimal("100"),
            step=Decimal("10"),
        )
        feature_set = default_feature_set()

        first = compute_daily_features(
            candidate,
            selection_date=SELECTION_DATE,
            as_of=AS_OF,
            source_snapshot_ids=("snapshot-candidate",),
            feature_set=feature_set,
            benchmark_bars=benchmark,
            benchmark_source_snapshot_ids=("snapshot-benchmark",),
        )
        second = compute_daily_features(
            candidate,
            selection_date=SELECTION_DATE,
            as_of=AS_OF,
            source_snapshot_ids=("snapshot-candidate",),
            feature_set=feature_set,
            benchmark_bars=benchmark,
            benchmark_source_snapshot_ids=("snapshot-benchmark",),
        )

        self.assertEqual(first, second)
        self.assertEqual(first.feature_set_version_id, feature_set.feature_set_version_id)
        self.assertEqual(first.source_snapshot_ids, ("snapshot-candidate", "snapshot-benchmark"))
        self.assertGreater(first.values["return_1"], Decimal("0"))
        self.assertEqual(first.values["return_20"], Decimal("2"))
        self.assertEqual(first.values["relative_strength_20"], Decimal("0"))

    def test_feature_snapshot_store_is_idempotent(self) -> None:
        snapshot = compute_daily_features(
            _bars("security-candidate", "000001.SZ", Decimal("10")),
            selection_date=SELECTION_DATE,
            as_of=AS_OF,
            source_snapshot_ids=("snapshot-candidate",),
            benchmark_bars=_bars(
                "security-benchmark",
                "000300.SH",
                Decimal("100"),
                step=Decimal("10"),
            ),
            benchmark_source_snapshot_ids=("snapshot-benchmark",),
        )
        with TemporaryDirectory() as directory:
            store = FeatureSnapshotStore(directory)
            self.assertEqual(store.write(snapshot), store.write(snapshot))
            self.assertEqual(store.load(snapshot.feature_snapshot_id), snapshot)

    def test_future_session_is_rejected(self) -> None:
        bars = _bars("security-candidate", "000001.SZ", Decimal("10"))
        future = _bar(
            "security-candidate",
            "000001.SZ",
            SELECTION_DATE,
            Decimal("40"),
            datetime(2026, 2, 1, 15, tzinfo=UTC),
        )
        with self.assertRaisesRegex(FeatureComputationError, "on or after selection_date"):
            compute_daily_features(
                (*bars, future),
                selection_date=SELECTION_DATE,
                as_of=AS_OF,
                source_snapshot_ids=("snapshot-candidate",),
                benchmark_bars=_bars(
                    "security-benchmark",
                    "000300.SH",
                    Decimal("100"),
                    step=Decimal("10"),
                ),
            )

    def test_unavailable_bar_is_rejected_at_as_of(self) -> None:
        bars = _bars(
            "security-candidate",
            "000001.SZ",
            Decimal("10"),
            available_at=datetime(2026, 2, 2, 8, tzinfo=UTC),
        )
        with self.assertRaisesRegex(FeatureComputationError, "not available at as_of"):
            compute_daily_features(
                bars,
                selection_date=SELECTION_DATE,
                as_of=AS_OF,
                source_snapshot_ids=("snapshot-candidate",),
                benchmark_bars=_bars("security-benchmark", "000300.SH", Decimal("100")),
            )

    def test_missing_benchmark_is_rejected(self) -> None:
        with self.assertRaisesRegex(FeatureComputationError, "requires benchmark"):
            compute_daily_features(
                _bars("security-candidate", "000001.SZ", Decimal("10")),
                selection_date=SELECTION_DATE,
                as_of=AS_OF,
                source_snapshot_ids=("snapshot-candidate",),
            )


def _bars(
    security_id: str,
    symbol: str,
    start_close: Decimal,
    *,
    available_at: datetime = datetime(2026, 1, 31, 16, tzinfo=UTC),
    step: Decimal = Decimal("1"),
) -> tuple[DailyBar, ...]:
    return tuple(
        _bar(
            security_id,
            symbol,
            date(2026, 1, 1) + timedelta(days=index),
            start_close + step * Decimal(index),
            available_at,
        )
        for index in range(21)
    )


def _bar(
    security_id: str,
    symbol: str,
    session: date,
    close: Decimal,
    available_at: datetime,
) -> DailyBar:
    return DailyBar(
        security_id=security_id,
        symbol=symbol,
        session_date=session,
        provider="fixture",
        provider_symbol=symbol,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000"),
        amount=close * Decimal("1000"),
        event_time=datetime.combine(session, datetime.min.time(), tzinfo=UTC),
        available_at=available_at,
        ingested_at=available_at,
        suspended=False,
        tradable=True,
        quality_state="approved",
    )


if __name__ == "__main__":
    unittest.main()
