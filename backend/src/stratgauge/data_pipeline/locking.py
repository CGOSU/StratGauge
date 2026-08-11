"""Cross-platform local write locks for single-user storage roots."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class LocalWriteLockError(RuntimeError):
    """A local storage root cannot be exclusively written right now."""


@contextmanager
def local_write_lock(
    root: str | Path,
    *,
    name: str = "write",
    timeout_seconds: float = 0.0,
    poll_seconds: float = 0.05,
) -> Iterator[None]:
    """Hold an advisory single-process lock for one local storage root.

    Windows uses ``msvcrt`` byte-range locking and POSIX uses ``flock``. The
    lock file is intentionally kept on disk so operators can see which root
    is protected; the operating system owns the lock state, not the file.
    """

    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must not be negative")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")

    lock_dir = Path(root) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"
    handle = lock_path.open("a+b")
    try:
        handle.seek(0)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                _acquire(handle)
                break
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise LocalWriteLockError(
                        f"local storage root is busy: {Path(root).resolve()}"
                    ) from exc
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            _release(handle)
    finally:
        handle.close()


def _acquire(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]


def _release(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
