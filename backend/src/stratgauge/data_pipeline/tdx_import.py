"""Parse manually exported TongdaXin selection files."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Final

from stratgauge.domain.models import Exchange
from stratgauge.domain.selections import (
    SelectionBatch,
    SelectionCandidate,
    TdxSelectionImport,
)


class TdxImportError(ValueError):
    """A TongdaXin export cannot be imported without guessing its meaning."""


_CODE_HEADERS: Final[frozenset[str]] = frozenset({"代码", "证券代码", "股票代码", "code", "symbol"})
_EXCHANGE_HEADERS: Final[frozenset[str]] = frozenset({"市场", "交易所", "exchange", "market"})
_NAME_HEADERS: Final[frozenset[str]] = frozenset({"名称", "证券名称", "股票名称", "name"})
_EXCHANGE_ALIASES: Final[dict[str, Exchange]] = {
    "SH": Exchange.SH,
    "SSE": Exchange.SH,
    "沪": Exchange.SH,
    "沪市": Exchange.SH,
    "上海": Exchange.SH,
    "上海证券交易所": Exchange.SH,
    "SZ": Exchange.SZ,
    "SZSE": Exchange.SZ,
    "深": Exchange.SZ,
    "深市": Exchange.SZ,
    "深圳": Exchange.SZ,
    "深圳证券交易所": Exchange.SZ,
    "BJ": Exchange.BJ,
    "BSE": Exchange.BJ,
    "北": Exchange.BJ,
    "北市": Exchange.BJ,
    "北京": Exchange.BJ,
    "北京证券交易所": Exchange.BJ,
}


def import_tdx_export(
    content: bytes,
    *,
    selection_date: date,
    exported_at: datetime,
    imported_at: datetime,
    formula_name: str,
    formula_version: str | None = None,
    formula_sha256: str | None = None,
) -> TdxSelectionImport:
    """Parse one TongdaXin CSV/TSV/TXT or market-prefixed ``.EBK`` export.

    EBK rows use the explicit seven-digit market-code form ``0xxxxxx`` for SZ,
    ``1xxxxxx`` for SH and ``2xxxxxx`` for BJ; no exchange is inferred from a
    six-digit code prefix.

    Exchange metadata is mandatory unless the code itself has an explicit ``.SH``,
    ``.SZ`` or ``.BJ`` suffix. The parser never infers an exchange from a code prefix.
    """

    if not content:
        raise TdxImportError("TongdaXin export is empty")
    source_file_sha256 = hashlib.sha256(content).hexdigest()
    text = _decode_content(content)
    ebk_rows = _parse_ebk_rows(text)
    if ebk_rows is None:
        delimiter = _detect_delimiter(text)
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        headers = _parse_headers(rows)
        indexes = _column_indexes(headers)
        data_rows = tuple(
            (line_number, row, False) for line_number, row in enumerate(rows[1:], start=2)
        )
    else:
        headers = ("代码",)
        indexes = _ColumnIndexes(code=0, exchange=None, name=None)
        data_rows = tuple((line_number, [code], True) for line_number, code in ebk_rows)
    batch_id = _batch_id(
        source_file_sha256=source_file_sha256,
        selection_date=selection_date,
        formula_name=formula_name,
        formula_version=formula_version,
        formula_sha256=formula_sha256,
    )

    candidates: list[SelectionCandidate] = []
    seen_symbols: set[str] = set()
    for line_number, row, is_ebk in data_rows:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) > len(headers):
            raise TdxImportError(f"line {line_number} contains more fields than the header")
        values = list(row) + [""] * (len(headers) - len(row))
        code_value = values[indexes.code].strip()
        exchange_value = values[indexes.exchange].strip() if indexes.exchange is not None else ""
        if is_ebk:
            code_value = _parse_ebk_security(code_value, line_number)
        local_code, exchange = _parse_security(code_value, exchange_value, line_number)
        symbol = f"{local_code}.{exchange.value}"
        if symbol in seen_symbols:
            raise TdxImportError(f"line {line_number} duplicates candidate {symbol}")
        seen_symbols.add(symbol)
        source_fields = {
            header: values[index].strip()
            for index, header in enumerate(headers)
            if values[index].strip()
        }
        name = values[indexes.name].strip() if indexes.name is not None else None
        candidates.append(
            SelectionCandidate(
                candidate_id=f"{batch_id}:{symbol}",
                batch_id=batch_id,
                local_code=local_code,
                symbol=symbol,
                exchange=exchange,
                name=name or None,
                source_fields=source_fields,
            )
        )

    batch = SelectionBatch(
        batch_id=batch_id,
        selection_date=selection_date,
        exported_at=exported_at,
        imported_at=imported_at,
        formula_name=formula_name,
        formula_version=formula_version,
        formula_sha256=formula_sha256,
        source_file_sha256=source_file_sha256,
        candidate_count=len(candidates),
    )
    return TdxSelectionImport(batch=batch, candidates=tuple(candidates))


class _ColumnIndexes:
    def __init__(self, code: int, exchange: int | None, name: int | None) -> None:
        self.code = code
        self.exchange = exchange
        self.name = name


def _decode_content(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise TdxImportError("TongdaXin export is not valid UTF-8 or GB18030 text")


def _parse_ebk_rows(text: str) -> tuple[tuple[int, str], ...] | None:
    rows = tuple(
        (line_number, line.strip())
        for line_number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    )
    if rows and all(re.fullmatch(r"\d{7}", code) for _, code in rows):
        return rows
    return None


def _parse_ebk_security(value: str, line_number: int) -> str:
    market = {"0": "SZ", "1": "SH", "2": "BJ"}.get(value[0])
    if market is None:
        raise TdxImportError(f"line {line_number} has an unsupported EBK market prefix")
    return f"{value[1:]}.{market}"


def _detect_delimiter(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            if "\t" in line:
                return "\t"
            if "," in line:
                return ","
            if ";" in line:
                return ";"
            break
    raise TdxImportError("TongdaXin export must be CSV, TSV or semicolon-delimited text")


def _parse_headers(rows: Sequence[list[str]]) -> tuple[str, ...]:
    if not rows:
        raise TdxImportError("TongdaXin export has no header row")
    headers = tuple(_normalize_header(header) for header in rows[0])
    if not headers or any(not header for header in headers):
        raise TdxImportError("TongdaXin export contains an empty header")
    if len(set(headers)) != len(headers):
        raise TdxImportError("TongdaXin export contains duplicate headers")
    return headers


def _column_indexes(headers: tuple[str, ...]) -> _ColumnIndexes:
    code = _find_header(headers, _CODE_HEADERS)
    if code is None:
        raise TdxImportError("TongdaXin export must contain a code column")
    return _ColumnIndexes(
        code=code,
        exchange=_find_header(headers, _EXCHANGE_HEADERS),
        name=_find_header(headers, _NAME_HEADERS),
    )


def _find_header(headers: tuple[str, ...], candidates: frozenset[str]) -> int | None:
    normalized_candidates = {_normalize_header(candidate) for candidate in candidates}
    for index, header in enumerate(headers):
        if header in normalized_candidates:
            return index
    return None


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def _parse_security(
    code_value: str,
    exchange_value: str,
    line_number: int,
) -> tuple[str, Exchange]:
    match = re.fullmatch(r"(\d{6})(?:\.([A-Za-z]{2}))?", code_value.upper())
    if match is None:
        raise TdxImportError(f"line {line_number} has an invalid security code")
    local_code = match.group(1)
    suffix = match.group(2)
    if suffix is not None:
        try:
            return local_code, Exchange(suffix)
        except ValueError as exc:
            raise TdxImportError(f"line {line_number} has an invalid exchange suffix") from exc
    if not exchange_value:
        raise TdxImportError(f"line {line_number} has no exchange; code-only rows are not inferred")
    exchange = _EXCHANGE_ALIASES.get(_normalize_exchange(exchange_value))
    if exchange is None:
        raise TdxImportError(f"line {line_number} has an unsupported exchange")
    return local_code, exchange


def _normalize_exchange(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().upper())


def _batch_id(
    *,
    source_file_sha256: str,
    selection_date: date,
    formula_name: str,
    formula_version: str | None,
    formula_sha256: str | None,
) -> str:
    identity = json.dumps(
        {
            "formula_name": formula_name.strip(),
            "formula_sha256": formula_sha256,
            "formula_version": formula_version.strip() if formula_version else None,
            "selection_date": selection_date.isoformat(),
            "source_file_sha256": source_file_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"tdx-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
