import unittest
from datetime import UTC, date, datetime

from pydantic import ValidationError

from stratgauge.domain.models import (
    ActionState,
    DailyBar,
    Exchange,
    Recommendation,
    Security,
    Signal,
    canonical_symbol,
)

UTC_TZ = UTC


def make_bar(**changes: object) -> DailyBar:
    values: dict[str, object] = {
        "security_id": "security-1",
        "symbol": "000001.SZ",
        "session_date": date(2026, 1, 5),
        "provider": "fixture",
        "provider_symbol": "000001",
        "open": "10.00",
        "high": "10.50",
        "low": "9.80",
        "close": "10.20",
        "previous_close": "10.00",
        "volume": "1000",
        "amount": "10200",
        "event_time": datetime(2026, 1, 5, 7, tzinfo=UTC_TZ),
        "available_at": datetime(2026, 1, 5, 8, tzinfo=UTC_TZ),
        "ingested_at": datetime(2026, 1, 5, 9, tzinfo=UTC_TZ),
    }
    values.update(changes)
    return DailyBar.model_validate(values)


class CanonicalSymbolTests(unittest.TestCase):
    def test_uses_explicit_exchange_metadata(self) -> None:
        self.assertEqual(canonical_symbol("000001", "SZ"), "000001.SZ")
        self.assertEqual(canonical_symbol("920001", "BJ"), "920001.BJ")

    def test_rejects_malformed_code(self) -> None:
        with self.assertRaises(ValueError):
            canonical_symbol("00001", "SZ")


class DomainModelTests(unittest.TestCase):
    def test_security_requires_matching_canonical_symbol(self) -> None:
        security = Security(
            security_id="security-1",
            local_code="000001",
            symbol="000001.SZ",
            exchange=Exchange.SZ,
        )
        self.assertEqual(security.symbol, "000001.SZ")

        with self.assertRaises(ValidationError):
            Security(
                security_id="security-1",
                local_code="000001",
                symbol="000001.SH",
                exchange=Exchange.SZ,
            )

    def test_daily_bar_rejects_invalid_range_and_naive_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            make_bar(high="9.00")
        with self.assertRaises(ValidationError):
            make_bar(event_time=datetime(2026, 1, 5, 7))

    def test_daily_bar_rejects_suspended_tradable_state(self) -> None:
        with self.assertRaises(ValidationError):
            make_bar(suspended=True, tradable=True)

    def test_signal_cannot_execute_on_signal_date(self) -> None:
        with self.assertRaises(ValidationError):
            Signal(
                signal_id="signal-1",
                security_id="security-1",
                symbol="000001.SZ",
                as_of=date(2026, 1, 5),
                earliest_execution=date(2026, 1, 5),
                latest_execution=date(2026, 1, 7),
                strategy_version_id="strategy-1-v1",
                feature_set_version="features-v1",
                source_snapshot_id="snapshot-1",
            )

    def test_recommendation_keeps_probability_optional(self) -> None:
        recommendation = Recommendation(
            recommendation_id="recommendation-1",
            security_id="security-1",
            symbol="000001.SZ",
            as_of=date(2026, 1, 5),
            intended_session=date(2026, 1, 6),
            action_state=ActionState.WAIT_ENTRY,
            strategy_version_id="strategy-1-v1",
            feature_set_version="features-v1",
            source_snapshot_id="snapshot-1",
        )
        self.assertIsNone(recommendation.probability)


if __name__ == "__main__":
    unittest.main()
