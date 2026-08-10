import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory

from stratgauge.domain.models import StrategyLifecycle, StrategyVersion
from stratgauge.strategies.store import StrategyStoreError, StrategyVersionStore


class StrategyVersionStoreTests(unittest.TestCase):
    def test_lifecycle_transitions_are_append_only_and_materialized(self) -> None:
        strategy = _strategy()
        with TemporaryDirectory() as directory:
            store = StrategyVersionStore(directory)
            self.assertEqual(store.create(strategy), strategy)
            validated = store.transition(
                strategy.strategy_version_id,
                StrategyLifecycle.VALIDATED,
                changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                reason="fixture validation passed",
            )
            shadow = store.transition(
                strategy.strategy_version_id,
                StrategyLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="shadow started",
            )

            self.assertEqual(validated.lifecycle, StrategyLifecycle.VALIDATED)
            self.assertEqual(shadow.lifecycle, StrategyLifecycle.SHADOW)
            self.assertEqual(len(store.list_events(strategy.strategy_version_id)), 2)
            self.assertEqual(store.create(strategy).lifecycle, StrategyLifecycle.SHADOW)

    def test_invalid_transition_and_non_advancing_time_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = StrategyVersionStore(directory)
            strategy = store.create(_strategy())
            with self.assertRaisesRegex(StrategyStoreError, "invalid strategy lifecycle"):
                store.transition(
                    strategy.strategy_version_id,
                    StrategyLifecycle.ACTIVE,
                    changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    reason="skip validation",
                )
            store.transition(
                strategy.strategy_version_id,
                StrategyLifecycle.VALIDATED,
                changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                reason="validated",
            )
            with self.assertRaisesRegex(StrategyStoreError, "timestamp must advance"):
                store.transition(
                    strategy.strategy_version_id,
                    StrategyLifecycle.SHADOW,
                    changed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    reason="same timestamp",
                )


def _strategy() -> StrategyVersion:
    return StrategyVersion(
        strategy_version_id="strategy-fixture-v1",
        strategy_id="strategy-fixture",
        version=1,
        name="Fixture strategy",
        feature_set_version="features-fixture-v1",
        lifecycle=StrategyLifecycle.DRAFT,
    )


if __name__ == "__main__":
    unittest.main()
