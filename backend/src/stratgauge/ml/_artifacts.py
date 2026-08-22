"""Private filesystem primitives for immutable ML evidence bundles."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def write_immutable_bundle(
    destination: Path,
    outputs: Mapping[str, bytes | str],
    *,
    already_exists_message: str,
) -> None:
    """Write a fixed bundle once, removing partial output if a write fails."""

    destination.mkdir(parents=True, exist_ok=True)
    paths = tuple(destination / name for name in outputs)
    if any(path.exists() for path in paths):
        raise FileExistsError(already_exists_message)

    temporary_paths: list[Path] = []
    try:
        for path, content in zip(paths, outputs.values(), strict=True):
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            if isinstance(content, str):
                temporary.write_text(content, encoding="utf-8", newline="\n")
            else:
                temporary.write_bytes(content)
            temporary_paths.append(temporary)
        for temporary, path in zip(temporary_paths, paths, strict=True):
            temporary.replace(path)
    except OSError:
        for temporary in temporary_paths:
            temporary.unlink(missing_ok=True)
        for path in paths:
            path.unlink(missing_ok=True)
        raise
