"""Record an append-only human label for an imported TongdaXin candidate."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.domain.selections import ManualSelectionDecision, ManualSelectionLabel


def main() -> int:
    arguments = _parse_arguments()
    label = ManualSelectionLabel(
        candidate_id=arguments.candidate_id,
        decision=arguments.decision,
        labeled_at=arguments.labeled_at,
        note=arguments.note,
    )
    try:
        saved = SelectionImportStore(arguments.root).write_label(arguments.batch_id, label)
    except (OSError, SelectionStoreError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(saved.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/selection"))
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--decision", type=ManualSelectionDecision, required=True)
    parser.add_argument("--labeled-at", type=_parse_aware_datetime, default=datetime.now(UTC))
    parser.add_argument("--note")
    return parser.parse_args()


def _parse_aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone offset")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
