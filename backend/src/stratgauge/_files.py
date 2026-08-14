"""Small filesystem primitives shared by local evidence stores."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile


def atomic_write_text(path: Path, content: str) -> None:
    """Replace ``path`` with UTF-8 text through a unique sibling temporary file."""

    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
