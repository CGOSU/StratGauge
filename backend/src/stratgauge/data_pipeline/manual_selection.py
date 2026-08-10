"""Build immutable evidence for manually recorded selection batches."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import NamedTuple

from stratgauge.domain.models import Exchange, canonical_symbol
from stratgauge.domain.selections import SelectionBatch, SelectionCandidate, TdxSelectionImport


class ManualSelectionError(ValueError):
    """A manually recorded selection cannot be normalized safely."""


class ManualSelectionCandidateInput(NamedTuple):
    """Normalized input for one manually recorded candidate."""

    local_code: str
    exchange: Exchange
    name: str | None = None
    note: str | None = None


class ManualSelectionBuildResult(NamedTuple):
    """Canonical raw evidence and its parsed selection import."""

    raw_content: bytes
    imported: TdxSelectionImport


def build_manual_selection(
    *,
    selection_date: date,
    recorded_at: datetime,
    imported_at: datetime,
    formula_id: str,
    formula_name: str,
    formula_version: str | None,
    candidates: Sequence[ManualSelectionCandidateInput],
) -> ManualSelectionBuildResult:
    """Normalize a manual request and build its immutable selection evidence.

    The raw content contains only the normalized request fields.  Its UTF-8
    canonical JSON bytes are also the identity of the manual batch, so replaying
    an equivalent request addresses the same immutable import.
    """

    normalized_formula_id = formula_id.strip().lower()
    normalized_formula_name = formula_name.strip()
    normalized_formula_version = formula_version.strip() if formula_version else None
    if not candidates:
        raise ManualSelectionError("at least one candidate is required")
    if not normalized_formula_id:
        raise ManualSelectionError("formula_id must not be empty")
    if not normalized_formula_name:
        raise ManualSelectionError("formula name must not be empty")
    _require_aware_timestamp(recorded_at, "recorded_at")
    _require_aware_timestamp(imported_at, "imported_at")

    normalized_candidates: list[ManualSelectionCandidateInput] = []
    evidence_candidates: list[dict[str, str | None]] = []
    seen_symbols: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_candidate(candidate)
        symbol = canonical_symbol(normalized.local_code, normalized.exchange)
        if symbol in seen_symbols:
            raise ManualSelectionError(f"duplicate candidate symbol: {symbol}")
        seen_symbols.add(symbol)
        normalized_candidates.append(normalized)
        evidence_candidates.append(
            {
                "exchange": normalized.exchange.value,
                "local_code": normalized.local_code,
                "name": normalized.name,
                "note": normalized.note,
            }
        )

    evidence = {
        "candidates": evidence_candidates,
        "formula_id": normalized_formula_id,
        "recorded_at": recorded_at.isoformat(),
        "selection_date": selection_date.isoformat(),
    }
    raw_content = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    batch_id = f"manual-{hashlib.sha256(raw_content).hexdigest()[:20]}"
    parsed_candidates = tuple(
        _build_candidate(batch_id, candidate) for candidate in normalized_candidates
    )
    batch = SelectionBatch(
        batch_id=batch_id,
        source="manual",
        source_filename=f"manual-{selection_date.isoformat()}.json",
        formula_id=normalized_formula_id,
        selection_date=selection_date,
        exported_at=recorded_at,
        imported_at=imported_at,
        formula_name=normalized_formula_name,
        formula_version=normalized_formula_version,
        source_file_sha256=hashlib.sha256(raw_content).hexdigest(),
        candidate_count=len(parsed_candidates),
    )
    return ManualSelectionBuildResult(
        raw_content=raw_content,
        imported=TdxSelectionImport(batch=batch, candidates=parsed_candidates),
    )


def _normalize_candidate(
    candidate: ManualSelectionCandidateInput,
) -> ManualSelectionCandidateInput:
    local_code = candidate.local_code.strip().upper()
    if not re.fullmatch(r"\d{6}", local_code):
        raise ManualSelectionError("local_code must contain exactly six digits")
    try:
        exchange = Exchange(candidate.exchange)
    except ValueError as exc:
        raise ManualSelectionError("exchange must be SH, SZ or BJ") from exc
    return ManualSelectionCandidateInput(
        local_code=local_code,
        exchange=exchange,
        name=_normalize_optional_text(candidate.name),
        note=_normalize_optional_text(candidate.note),
    )


def _build_candidate(
    batch_id: str,
    candidate: ManualSelectionCandidateInput,
) -> SelectionCandidate:
    symbol = canonical_symbol(candidate.local_code, candidate.exchange)
    source_fields = {"note": candidate.note} if candidate.note is not None else {}
    return SelectionCandidate(
        candidate_id=f"{batch_id}:{symbol}",
        batch_id=batch_id,
        local_code=candidate.local_code,
        symbol=symbol,
        exchange=candidate.exchange,
        name=candidate.name,
        source_fields=source_fields,
    )


def _normalize_optional_text(value: str | None) -> str | None:
    return None if value is None else value.strip() or None


def _require_aware_timestamp(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManualSelectionError(f"{field_name} must include a timezone")
