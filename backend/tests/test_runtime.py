from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from stratgauge.runtime import DATA_ROOT_ENV, RuntimeConfig


class RuntimeConfigTests(unittest.TestCase):
    def test_all_local_roots_derive_from_one_data_root(self) -> None:
        config = RuntimeConfig.from_environment("runtime-test-data")

        self.assertEqual(config.data_root, Path("runtime-test-data").resolve())
        self.assertEqual(config.selection_root, config.data_root / "selection")
        self.assertEqual(config.market_root, config.data_root / "selection-market")
        self.assertEqual(config.portfolio_root, config.data_root / "portfolio")
        self.assertEqual(config.calendar_root, config.data_root / "calendars")
        self.assertEqual(config.model_root, config.data_root)
        self.assertEqual(config.universe_root, config.data_root)

    def test_environment_root_is_used_when_no_explicit_root_is_given(self) -> None:
        with patch.dict(os.environ, {DATA_ROOT_ENV: "environment-test-data"}, clear=False):
            config = RuntimeConfig.from_environment()

        self.assertEqual(config.data_root, Path("environment-test-data").resolve())

    def test_explicit_store_roots_are_kept_for_tests_and_migrations(self) -> None:
        root = Path("runtime-migration-data")
        config = RuntimeConfig.from_environment(
            root,
            selection_root=root / "legacy-selection",
            market_root=root / "legacy-market",
        )

        self.assertEqual(config.selection_root, (root / "legacy-selection").resolve())
        self.assertEqual(config.market_root, (root / "legacy-market").resolve())


if __name__ == "__main__":
    unittest.main()
