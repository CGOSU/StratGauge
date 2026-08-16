"""Formula catalog and selection import persistence."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.data_pipeline.selection_store._base import (
    SelectionImportManifest,
    SelectionStoreBase,
    SelectionStoreError,
)
from stratgauge.data_pipeline.selection_store._io import _atomic_write_bytes, _write_selection_text
from stratgauge.domain.selections import (
    SelectionFormula,
    TdxSelectionImport,
)


class FormulasMixin(SelectionStoreBase):
    """Formula catalog and raw import operations."""

    def create_formula(
        self,
        *,
        name: str,
        version: str | None = None,
        description: str | None = None,
    ) -> SelectionFormula:
        with local_write_lock(self.root):
            return self._create_formula_unlocked(
                name=name,
                version=version,
                description=description,
            )

    def _create_formula_unlocked(
        self,
        *,
        name: str,
        version: str | None = None,
        description: str | None = None,
    ) -> SelectionFormula:
        normalized_name = name.strip()
        normalized_version = version.strip() if version else None
        identity = f"{normalized_name}\0{normalized_version or ''}"
        formula_id = f"formula-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
        formula = SelectionFormula(
            formula_id=formula_id,
            name=normalized_name,
            version=normalized_version,
            description=description,
            created_at=datetime.now(UTC),
        )
        path = self.formula_dir / f"{formula_id}.json"
        if path.exists():
            existing = self.load_formula(formula_id)
            if (
                existing.name != formula.name
                or existing.version != formula.version
                or existing.description != formula.description
            ):
                raise SelectionStoreError("formula identity already exists with different metadata")
            return existing
        _write_selection_text(path, formula.model_dump_json(indent=2))
        return formula

    def list_formulas(self, *, include_archived: bool = False) -> tuple[SelectionFormula, ...]:
        try:
            formulas = tuple(
                SelectionFormula.model_validate_json(path.read_text(encoding="utf-8"))
                for path in self.formula_dir.glob("formula-*.json")
            )
        except Exception as exc:
            raise SelectionStoreError("selection formula is invalid") from exc
        ordered = tuple(
            sorted(
                formulas, key=lambda formula: (formula.created_at, formula.formula_id), reverse=True
            )
        )
        if include_archived:
            return ordered
        return tuple(
            formula for formula in ordered if self.is_entity_active("formula", formula.formula_id)
        )

    def load_formula(self, formula_id: str) -> SelectionFormula:
        if not re.fullmatch(r"formula-[0-9a-f]{20}", formula_id):
            raise SelectionStoreError(f"invalid selection formula id: {formula_id}")
        path = self.formula_dir / f"{formula_id}.json"
        if not path.exists():
            raise SelectionStoreError(f"selection formula does not exist: {formula_id}")
        try:
            return SelectionFormula.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection formula is invalid: {formula_id}") from exc

    def write(
        self,
        raw_content: bytes,
        parsed: TdxSelectionImport,
    ) -> SelectionImportManifest:
        with local_write_lock(self.root):
            return self._write_unlocked(raw_content, parsed)

    def _write_unlocked(
        self,
        raw_content: bytes,
        parsed: TdxSelectionImport,
    ) -> SelectionImportManifest:
        actual_hash = hashlib.sha256(raw_content).hexdigest()
        if actual_hash != parsed.batch.source_file_sha256:
            raise SelectionStoreError("raw content does not match parsed import hash")

        manifest_path = self.manifest_dir / f"{parsed.batch.batch_id}.json"
        if manifest_path.exists():
            manifest = self.load_manifest(parsed.batch.batch_id)
            if manifest.source_file_sha256 != actual_hash:
                raise SelectionStoreError("existing selection manifest has a different hash")
            return manifest

        raw_path = self.raw_dir / f"{actual_hash}.bin"
        import_path = self.import_dir / f"{parsed.batch.batch_id}.json"
        _atomic_write_bytes(raw_path, raw_content)
        _write_selection_text(import_path, parsed.model_dump_json(indent=2))
        manifest = SelectionImportManifest(
            batch_id=parsed.batch.batch_id,
            source_file_sha256=actual_hash,
            candidate_count=len(parsed.candidates),
            raw_path=str(raw_path.relative_to(self.root)),
            import_path=str(import_path.relative_to(self.root)),
        )
        _write_selection_text(manifest_path, manifest.model_dump_json(indent=2))
        return manifest

    def load_import(self, batch_id: str) -> TdxSelectionImport:
        manifest = self.load_manifest(batch_id)
        path = self.root / manifest.import_path
        if not path.exists():
            raise SelectionStoreError(f"selection import does not exist: {batch_id}")
        try:
            return TdxSelectionImport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection import is invalid: {batch_id}") from exc
