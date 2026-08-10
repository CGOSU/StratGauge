"""Immutable storage for manually imported selection exports."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field

from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections import (
    ManualSelectionLabel,
    SelectionFormula,
    SelectionLifecycleEvent,
    SelectionLifecycleStatus,
    SelectionOutcome,
    TdxSelectionImport,
)


class SelectionStoreError(RuntimeError):
    """A selection import cannot be safely persisted or read."""


class SelectionImportManifest(DomainModel):
    """File references and identity for one imported selection batch."""

    batch_id: str
    source_file_sha256: str
    candidate_count: int = Field(ge=0)
    raw_path: str
    import_path: str


class SelectionImportStore:
    """Persist raw exports and parsed candidates without overwriting evidence."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.raw_dir = self.root / "tdx" / "raw"
        self.import_dir = self.root / "tdx" / "imports"
        self.manifest_dir = self.root / "tdx" / "manifests"
        self.label_dir = self.root / "tdx" / "labels"
        self.outcome_dir = self.root / "tdx" / "outcomes"
        self.formula_dir = self.root / "tdx" / "formulas"
        self.lifecycle_dir = self.root / "tdx" / "lifecycle"
        for directory in (
            self.raw_dir,
            self.import_dir,
            self.manifest_dir,
            self.label_dir,
            self.outcome_dir,
            self.formula_dir,
            self.lifecycle_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def create_formula(
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
        _atomic_write_text(path, formula.model_dump_json(indent=2))
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
        _atomic_write_text(import_path, parsed.model_dump_json(indent=2))
        manifest = SelectionImportManifest(
            batch_id=parsed.batch.batch_id,
            source_file_sha256=actual_hash,
            candidate_count=len(parsed.candidates),
            raw_path=str(raw_path.relative_to(self.root)),
            import_path=str(import_path.relative_to(self.root)),
        )
        _atomic_write_text(manifest_path, manifest.model_dump_json(indent=2))
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

    def write_label(self, batch_id: str, label: ManualSelectionLabel) -> ManualSelectionLabel:
        imported = self.load_import(batch_id)
        if not any(
            candidate.candidate_id == label.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {label.candidate_id}"
            )
        if label.labeled_at < imported.batch.exported_at:
            raise SelectionStoreError("label timestamp precedes selection export")
        freeze_deadline = datetime.combine(
            imported.batch.selection_date + timedelta(days=1),
            time.min,
            tzinfo=imported.batch.exported_at.tzinfo,
        )
        if label.labeled_at >= freeze_deadline:
            raise SelectionStoreError("label timestamp is after the selection freeze deadline")
        label_id = (
            f"label-{hashlib.sha256(label.model_dump_json().encode('utf-8')).hexdigest()[:20]}"
        )
        path = self.label_dir / f"{label_id}.json"
        if path.exists():
            try:
                return ManualSelectionLabel.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection label is invalid: {label_id}") from exc
        _atomic_write_text(path, label.model_dump_json(indent=2))
        return label

    def write_outcome(self, outcome: SelectionOutcome) -> SelectionOutcome:
        imported = self.load_import(outcome.batch_id)
        if not any(
            candidate.candidate_id == outcome.candidate_id for candidate in imported.candidates
        ):
            raise SelectionStoreError(
                f"candidate does not belong to selection batch: {outcome.candidate_id}"
            )
        outcome_id = (
            f"outcome-{hashlib.sha256(outcome.model_dump_json().encode('utf-8')).hexdigest()[:20]}"
        )
        path = self.outcome_dir / f"{outcome_id}.json"
        if path.exists():
            try:
                return SelectionOutcome.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection outcome is invalid: {outcome_id}") from exc
        _atomic_write_text(path, outcome.model_dump_json(indent=2))
        return outcome

    def list_outcomes(self, candidate_id: str) -> tuple[SelectionOutcome, ...]:
        outcomes: list[SelectionOutcome] = []
        for path in sorted(self.outcome_dir.glob("outcome-*.json")):
            try:
                outcome = SelectionOutcome.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection outcome is invalid: {path.name}") from exc
            if outcome.candidate_id == candidate_id:
                outcomes.append(outcome)
        return tuple(sorted(outcomes, key=lambda outcome: outcome.evaluated_at))

    def list_labels(self, candidate_id: str) -> tuple[ManualSelectionLabel, ...]:
        labels: list[ManualSelectionLabel] = []
        for path in sorted(self.label_dir.glob("label-*.json")):
            try:
                label = ManualSelectionLabel.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(f"selection label is invalid: {path.name}") from exc
            if label.candidate_id == candidate_id:
                labels.append(label)
        return tuple(sorted(labels, key=lambda label: label.labeled_at))

    def list_manifests(
        self, *, include_archived: bool = False
    ) -> tuple[SelectionImportManifest, ...]:
        manifests = tuple(
            self.load_manifest(path.stem) for path in sorted(self.manifest_dir.glob("*.json"))
        )
        if include_archived:
            return manifests
        return tuple(
            manifest for manifest in manifests if self.is_entity_active("batch", manifest.batch_id)
        )

    def list_lifecycle_events(
        self,
        entity_type: Literal["formula", "batch"],
        entity_id: str,
    ) -> tuple[SelectionLifecycleEvent, ...]:
        events: list[SelectionLifecycleEvent] = []
        try:
            for path in self.lifecycle_dir.glob("lifecycle-*.json"):
                event = SelectionLifecycleEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if event.entity_type == entity_type and event.entity_id == entity_id:
                    events.append(event)
        except Exception as exc:
            raise SelectionStoreError("selection lifecycle event is invalid") from exc
        return tuple(sorted(events, key=lambda event: (event.changed_at, event.event_id)))

    def is_entity_active(self, entity_type: Literal["formula", "batch"], entity_id: str) -> bool:
        events = self.list_lifecycle_events(entity_type, entity_id)
        return not events or events[-1].status is SelectionLifecycleStatus.ACTIVE

    def set_formula_status(
        self,
        formula_id: str,
        status: SelectionLifecycleStatus,
        *,
        changed_at: datetime,
        reason: str | None = None,
    ) -> SelectionLifecycleEvent:
        self.load_formula(formula_id)
        return self._set_entity_status(
            entity_type="formula",
            entity_id=formula_id,
            status=status,
            changed_at=changed_at,
            reason=reason,
        )

    def set_batch_status(
        self,
        batch_id: str,
        status: SelectionLifecycleStatus,
        *,
        changed_at: datetime,
        reason: str | None = None,
    ) -> SelectionLifecycleEvent:
        self.load_import(batch_id)
        return self._set_entity_status(
            entity_type="batch",
            entity_id=batch_id,
            status=status,
            changed_at=changed_at,
            reason=reason,
        )

    def load_raw(self, batch_id: str) -> bytes:
        manifest = self.load_manifest(batch_id)
        path = self.root / manifest.raw_path
        if not path.exists():
            raise SelectionStoreError(f"selection raw file does not exist: {batch_id}")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise SelectionStoreError(f"failed to read selection raw file: {batch_id}") from exc

    def _set_entity_status(
        self,
        *,
        entity_type: Literal["formula", "batch"],
        entity_id: str,
        status: SelectionLifecycleStatus,
        changed_at: datetime,
        reason: str | None,
    ) -> SelectionLifecycleEvent:
        events = self.list_lifecycle_events(entity_type, entity_id)
        if events and events[-1].status is status:
            return events[-1]
        identity = "\0".join(
            (entity_type, entity_id, status.value, changed_at.isoformat(), reason or "")
        )
        event_id = f"lifecycle-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
        event = SelectionLifecycleEvent(
            event_id=event_id,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            changed_at=changed_at,
            reason=reason,
        )
        path = self.lifecycle_dir / f"{event_id}.json"
        if path.exists():
            try:
                return SelectionLifecycleEvent.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SelectionStoreError(
                    f"selection lifecycle event is invalid: {event_id}"
                ) from exc
        _atomic_write_text(path, event.model_dump_json(indent=2))
        return event

    def load_manifest(self, batch_id: str) -> SelectionImportManifest:
        path = self.manifest_dir / f"{batch_id}.json"
        if not path.exists():
            raise SelectionStoreError(f"selection manifest does not exist: {batch_id}")
        try:
            return SelectionImportManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SelectionStoreError(f"selection manifest is invalid: {batch_id}") from exc


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SelectionStoreError(f"failed to write selection file: {path}") from exc


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SelectionStoreError(f"failed to write selection file: {path}") from exc
