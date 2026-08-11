"""Local runtime configuration shared by the API and operational commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_ROOT = Path("data")
DATA_ROOT_ENV = "STRATGAUGE_DATA_ROOT"


@dataclass(frozen=True)
class RuntimeConfig:
    """Resolve every local storage root from one canonical data directory.

    Individual roots remain overridable for tests and migration tools, but
    production-style entry points should construct this object once and pass
    its values down instead of inventing their own relative paths.
    """

    data_root: Path
    selection_root: Path
    market_root: Path
    portfolio_root: Path
    model_root: Path
    universe_root: Path
    calendar_root: Path
    live_verification_root: Path

    @classmethod
    def from_environment(
        cls,
        data_root: str | Path | None = None,
        *,
        selection_root: str | Path | None = None,
        market_root: str | Path | None = None,
        portfolio_root: str | Path | None = None,
        model_root: str | Path | None = None,
        universe_root: str | Path | None = None,
        calendar_root: str | Path | None = None,
        live_verification_root: str | Path | None = None,
    ) -> RuntimeConfig:
        """Build local paths, using ``STRATGAUGE_DATA_ROOT`` when provided."""

        configured_root = data_root or os.environ.get(DATA_ROOT_ENV) or DEFAULT_DATA_ROOT
        root = _absolute(Path(configured_root))
        return cls(
            data_root=root,
            selection_root=_under(root, selection_root, "selection"),
            market_root=_under(root, market_root, "selection-market"),
            portfolio_root=_under(root, portfolio_root, "portfolio"),
            # These stores currently own their child directories themselves:
            # ModelRegistryStore/ShadowScoreStore use ``data/models`` and
            # ``data/shadow-scores``; the universe stores use ``data/universe``.
            model_root=_absolute(Path(model_root)) if model_root is not None else root,
            universe_root=_absolute(Path(universe_root)) if universe_root is not None else root,
            calendar_root=_under(root, calendar_root, "calendars"),
            live_verification_root=_under(root, live_verification_root, "live-verification"),
        )

    def describe(self) -> dict[str, str]:
        """Return stable, JSON-friendly paths for startup and run logging."""

        return {
            "data_root": str(self.data_root),
            "selection_root": str(self.selection_root),
            "market_root": str(self.market_root),
            "portfolio_root": str(self.portfolio_root),
            "model_root": str(self.model_root),
            "universe_root": str(self.universe_root),
            "calendar_root": str(self.calendar_root),
            "live_verification_root": str(self.live_verification_root),
        }


def _under(root: Path, override: str | Path | None, child: str) -> Path:
    return _absolute(Path(override)) if override is not None else root / child


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()
