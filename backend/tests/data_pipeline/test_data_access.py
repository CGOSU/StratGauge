import unittest

from stratgauge.data_pipeline.data_access import (
    DataAccessDecision,
    build_data_access_plan,
)


class DataAccessPlanTests(unittest.TestCase):
    def test_local_snapshot_wins_without_remote_request(self) -> None:
        plan = build_data_access_plan(
            request_kind="market_daily",
            provider="tushare",
            cache_key="market_daily|key",
            local_snapshot_id="snapshot-1",
            remote_allowed=True,
        )

        self.assertEqual(plan.decision, DataAccessDecision.CACHE_HIT)
        self.assertTrue(plan.local_available)
        self.assertEqual(plan.local_snapshot_id, "snapshot-1")

    def test_cache_miss_can_be_remote_or_local_only(self) -> None:
        remote_plan = build_data_access_plan(
            request_kind="security_daily",
            provider="tushare",
            cache_key="security_daily|key",
            remote_allowed=True,
        )
        local_plan = build_data_access_plan(
            request_kind="security_daily",
            provider="tushare",
            cache_key="security_daily|key",
            remote_allowed=False,
        )

        self.assertEqual(remote_plan.decision, DataAccessDecision.REMOTE_FETCH)
        self.assertEqual(local_plan.decision, DataAccessDecision.LOCAL_ONLY)

    def test_forced_refresh_is_blocked_when_remote_is_disabled(self) -> None:
        plan = build_data_access_plan(
            request_kind="market_daily",
            provider="tushare",
            cache_key="market_daily|key",
            local_snapshot_id="snapshot-1",
            remote_allowed=False,
            force_refresh=True,
        )

        self.assertEqual(plan.decision, DataAccessDecision.BLOCKED)
        self.assertIn("remote access is disabled", plan.reason)


if __name__ == "__main__":
    unittest.main()
