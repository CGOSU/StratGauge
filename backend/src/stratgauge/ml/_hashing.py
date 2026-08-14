"""Shared hashing helpers for deterministic ML identities and evidence."""

from __future__ import annotations

import hashlib
import json


def canonical_json(value: object) -> bytes:
    """Serialize a value as compact, sorted UTF-8 JSON."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of byte content."""

    return hashlib.sha256(content).hexdigest()


def sha256_json(value: object) -> str:
    """Return the SHA-256 digest of canonical JSON content."""

    return sha256_bytes(canonical_json(value))
