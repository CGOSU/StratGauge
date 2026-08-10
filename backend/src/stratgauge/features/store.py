"""Immutable local storage for feature snapshots."""

from __future__ import annotations

from pathlib import Path

from stratgauge.features.contract import FeatureSnapshot


class FeatureStoreError(RuntimeError):
    """A feature snapshot cannot be safely persisted or read."""


class FeatureSnapshotStore:
    """Persist content-addressed feature snapshots without overwriting them."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.snapshot_dir = self.root / "features" / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def write(self, snapshot: FeatureSnapshot) -> FeatureSnapshot:
        path = self.snapshot_dir / f"{snapshot.feature_snapshot_id}.json"
        if path.exists():
            existing = self.load(snapshot.feature_snapshot_id)
            if existing != snapshot:
                raise FeatureStoreError("feature snapshot identity already has different content")
            return existing
        try:
            path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        except OSError as exc:
            raise FeatureStoreError("failed to write feature snapshot") from exc
        return snapshot

    def load(self, feature_snapshot_id: str) -> FeatureSnapshot:
        path = self.snapshot_dir / f"{feature_snapshot_id}.json"
        if not path.exists():
            raise FeatureStoreError(f"feature snapshot does not exist: {feature_snapshot_id}")
        try:
            return FeatureSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FeatureStoreError(f"feature snapshot is invalid: {feature_snapshot_id}") from exc

    def list_snapshots(self) -> tuple[FeatureSnapshot, ...]:
        return tuple(
            sorted(
                (
                    self.load(path.stem)
                    for path in self.snapshot_dir.glob("feature-snapshot-*.json")
                ),
                key=lambda snapshot: (snapshot.selection_date, snapshot.feature_snapshot_id),
            )
        )
