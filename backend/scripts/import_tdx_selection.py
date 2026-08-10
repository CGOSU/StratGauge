"""Import a manually exported TongdaXin selection file."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import TdxImportError, import_tdx_export


def main() -> int:
    arguments = _parse_arguments()
    try:
        content = arguments.file.read_bytes()
        parsed = import_tdx_export(
            content,
            selection_date=arguments.selection_date,
            exported_at=arguments.exported_at,
            imported_at=arguments.imported_at,
            source_filename=arguments.file.name,
            formula_name=arguments.formula_name,
            formula_version=arguments.formula_version,
            formula_sha256=arguments.formula_sha256,
        )
        manifest = SelectionImportStore(arguments.root).write(content, parsed)
    except (OSError, TdxImportError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--selection-date", type=date.fromisoformat, required=True)
    parser.add_argument("--exported-at", type=_parse_aware_datetime, required=True)
    parser.add_argument(
        "--imported-at",
        type=_parse_aware_datetime,
        default=datetime.now(UTC),
    )
    parser.add_argument("--formula-name", required=True)
    parser.add_argument("--formula-version")
    parser.add_argument("--formula-sha256")
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    arguments = parser.parse_args()
    if not arguments.file.is_file():
        parser.error(f"--file does not exist: {arguments.file}")
    return arguments


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
