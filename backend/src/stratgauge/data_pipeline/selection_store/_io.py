"""Low-level write helpers for the selection store."""

from __future__ import annotations

from pathlib import Path

from stratgauge._files import atomic_write_text
from stratgauge.data_pipeline.selection_store._base import SelectionStoreError


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SelectionStoreError(f"failed to write selection file: {path}") from exc


def _write_selection_text(path: Path, content: str) -> None:
    try:
        atomic_write_text(path, content)
    except OSError as exc:
        raise SelectionStoreError(f"failed to write selection file: {path}") from exc
