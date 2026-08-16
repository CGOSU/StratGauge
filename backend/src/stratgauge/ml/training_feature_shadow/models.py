"""Purpose-aware, non-authoritative shadow scoring for feature LightGBM models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stratgauge.data_pipeline.locking import local_write_lock
from stratgauge.domain.models import DomainModel
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml._hashing import (
    sha256_json,
)
from stratgauge.ml.training_datasets import (
    TrainingDatasetPurpose,
    TrainingLabel,
    label_contract_id_for,
)
from stratgauge.ml.training_feature_shadow.core import (
    FeatureShadowError,
)

FEATURE_SHADOW_SCORE_VERSION = "selection-purpose-feature-shadow-score-v1"


FEATURE_SHADOW_REPORT_VERSION = "selection-purpose-feature-shadow-report-v1"


FeatureShadowCalibrationMode = Literal["none", "registered"]


class FeatureShadowInput(DomainModel):
    """A purpose-bound candidate and point-in-time feature snapshot."""

    candidate_id: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    feature_snapshot: FeatureSnapshot

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature shadow candidate_id must not be empty")
        return normalized

    @field_validator("label_contract_id")
    @classmethod
    def validate_label_contract_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature shadow label contract must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_purpose_contract(self) -> FeatureShadowInput:
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature shadow input label contract does not match purpose")
        return self


class FeatureShadowScore(DomainModel):
    """One immutable purpose-aware score kept outside decisions and outcomes."""

    score_id: str
    score_version: Literal["selection-purpose-feature-shadow-score-v1"] = (
        "selection-purpose-feature-shadow-score-v1"
    )
    registration_id: str
    model_version_id: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    lifecycle: Literal["shadow"] = "shadow"
    candidate_id: str
    symbol: str
    selection_date: date
    feature_set_version_id: str
    feature_snapshot_id: str
    feature_as_of: datetime
    feature_available_at: datetime
    source_snapshot_ids: tuple[str, ...]
    scored_at: datetime
    class_labels: tuple[TrainingLabel, ...]
    predicted_label: TrainingLabel
    raw_probabilities: dict[str, Decimal]
    raw_predicted_label: TrainingLabel
    calibration_mode: FeatureShadowCalibrationMode = "none"
    calibration_id: str | None = None
    calibrated_probabilities: dict[str, Decimal] | None = None
    calibrated: bool = False
    used_for_decision: Literal[False] = False

    @field_validator(
        "score_id",
        "registration_id",
        "model_version_id",
        "label_contract_id",
        "candidate_id",
        "symbol",
        "feature_set_version_id",
        "feature_snapshot_id",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature shadow score text must not be empty")
        return normalized

    @field_validator("feature_as_of", "feature_available_at", "scored_at")
    @classmethod
    def validate_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature shadow score timestamps must include a timezone")
        return value

    @field_validator("source_snapshot_ids")
    @classmethod
    def validate_source_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("feature shadow scores require source snapshot IDs")
        return tuple(item.strip() for item in value)

    @model_validator(mode="after")
    def validate_contract(self) -> FeatureShadowScore:
        if self.score_version != FEATURE_SHADOW_SCORE_VERSION:
            raise ValueError("unsupported feature shadow score version")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature shadow score label contract does not match purpose")
        if self.class_labels != _labels_for(self.purpose):
            raise ValueError("feature shadow score classes do not match purpose")
        if self.predicted_label not in self.class_labels:
            raise ValueError("feature shadow predicted label does not match purpose")
        if self.feature_available_at > self.feature_as_of:
            raise ValueError("feature shadow feature availability follows feature as_of")
        if self.feature_as_of > self.scored_at:
            raise ValueError("feature shadow feature is not available at scored_at")
        if self.feature_as_of.date() > self.selection_date:
            raise ValueError("feature shadow feature as_of follows selection date")
        if self.selection_date > self.scored_at.date():
            raise ValueError("feature shadow selection date follows scored_at")
        _validate_probability_map(self.raw_probabilities, self.class_labels, "raw")
        if self.raw_predicted_label not in self.class_labels:
            raise ValueError("feature shadow raw predicted label does not match purpose")
        if self.raw_predicted_label is not _predicted_label(
            self.raw_probabilities, self.class_labels
        ):
            raise ValueError("feature shadow raw predicted label does not match probabilities")
        if self.calibration_mode == "registered":
            if not self.calibrated or self.calibration_id is None:
                raise ValueError("registered calibration mode requires calibrated evidence")
            if self.calibrated_probabilities is None:
                raise ValueError("registered calibration mode requires calibrated probabilities")
            _validate_probability_map(
                self.calibrated_probabilities,
                self.class_labels,
                "calibrated",
            )
            if self.predicted_label is not _predicted_label(
                self.calibrated_probabilities, self.class_labels
            ):
                raise ValueError(
                    "feature shadow predicted label does not match calibrated probabilities"
                )
        elif self.calibrated or self.calibration_id is not None or self.calibrated_probabilities:
            raise ValueError("uncalibrated shadow score contains calibration evidence")
        return self


class FeatureShadowFailure(DomainModel):
    """One candidate-level failure retained without changing source evidence."""

    candidate_id: str
    feature_snapshot_id: str
    reason: str

    @field_validator("candidate_id", "feature_snapshot_id", "reason")
    @classmethod
    def validate_failure_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("feature shadow failure text must not be empty")
        return normalized


class FeatureShadowReport(DomainModel):
    """Immutable accounting for one manually enabled shadow scoring run."""

    report_id: str
    report_version: Literal["selection-purpose-feature-shadow-report-v1"] = (
        "selection-purpose-feature-shadow-report-v1"
    )
    registration_id: str
    model_version_id: str
    purpose: TrainingDatasetPurpose
    label_contract_id: str
    lifecycle: Literal["shadow"] = "shadow"
    calibration_mode: FeatureShadowCalibrationMode = "none"
    calibration_id: str | None = None
    scored_at: datetime
    source_input_sha256: str
    input_count: int = Field(gt=0)
    score_ids: tuple[str, ...]
    failures: tuple[FeatureShadowFailure, ...]
    used_for_decision: Literal[False] = False

    @field_validator(
        "report_id",
        "registration_id",
        "model_version_id",
        "label_contract_id",
        "source_input_sha256",
    )
    @classmethod
    def validate_report_text(cls, value: str) -> str:
        normalized = value.strip().lower() if "sha256" in value else value.strip()
        if not normalized:
            raise ValueError("feature shadow report text must not be empty")
        return normalized

    @field_validator("scored_at")
    @classmethod
    def validate_scored_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("feature shadow scored_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> FeatureShadowReport:
        if self.report_version != FEATURE_SHADOW_REPORT_VERSION:
            raise ValueError("unsupported feature shadow report version")
        if not _is_sha256(self.source_input_sha256):
            raise ValueError("feature shadow input hash must be a SHA-256 digest")
        if len(self.score_ids) + len(self.failures) != self.input_count:
            raise ValueError("feature shadow report input accounting does not reconcile")
        if len(self.score_ids) != len(set(self.score_ids)):
            raise ValueError("feature shadow report contains duplicate score IDs")
        failure_candidate_ids = tuple(item.candidate_id for item in self.failures)
        if len(failure_candidate_ids) != len(set(failure_candidate_ids)):
            raise ValueError("feature shadow report contains duplicate failures")
        if self.label_contract_id != label_contract_id_for(self.purpose):
            raise ValueError("feature shadow report label contract does not match purpose")
        if self.calibration_mode == "registered" and self.calibration_id is None:
            raise ValueError("registered calibration mode requires calibration ID")
        if self.calibration_mode == "none" and self.calibration_id is not None:
            raise ValueError("uncalibrated feature shadow report contains calibration ID")
        return self


class FeatureShadowScoreStore:
    """Persist purpose-aware shadow scores and reports without overwriting."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.shadow_root = self.root / "models" / "feature-registry" / "shadow-scores"
        self.score_dir = self.shadow_root / "scores"
        self.report_dir = self.shadow_root / "reports"
        self.score_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def write_score(self, score: FeatureShadowScore) -> FeatureShadowScore:
        with local_write_lock(self.root, name="feature-model-shadow"):
            path = self.score_dir / f"{_safe_identifier(score.score_id)}.json"
            _write_once(path, (score.model_dump_json(indent=2) + "\n").encode("utf-8"))
            return self.load_score(score.score_id)

    def load_score(self, score_id: str) -> FeatureShadowScore:
        path = self.score_dir / f"{_safe_identifier(score_id)}.json"
        if not path.exists():
            raise FeatureShadowError(f"feature shadow score does not exist: {score_id}")
        try:
            score = FeatureShadowScore.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise FeatureShadowError(f"feature shadow score is invalid: {score_id}") from exc
        if score.score_id != _score_id(score):
            raise FeatureShadowError("feature shadow score content does not match its identity")
        return score

    def list_scores(self, *, candidate_id: str | None = None) -> tuple[FeatureShadowScore, ...]:
        scores = tuple(self.load_score(path.stem) for path in self.score_dir.glob("*.json"))
        if candidate_id is not None:
            scores = tuple(score for score in scores if score.candidate_id == candidate_id)
        return tuple(sorted(scores, key=lambda item: (item.scored_at, item.score_id)))

    def write_report(self, report: FeatureShadowReport) -> FeatureShadowReport:
        with local_write_lock(self.root, name="feature-model-shadow"):
            path = self.report_dir / f"{_safe_identifier(report.report_id)}.json"
            _write_once(path, (report.model_dump_json(indent=2) + "\n").encode("utf-8"))
            return self.load_report(report.report_id)

    def load_report(self, report_id: str) -> FeatureShadowReport:
        path = self.report_dir / f"{_safe_identifier(report_id)}.json"
        if not path.exists():
            raise FeatureShadowError(f"feature shadow report does not exist: {report_id}")
        try:
            report = FeatureShadowReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise FeatureShadowError(f"feature shadow report is invalid: {report_id}") from exc
        if report.report_id != _report_id(report):
            raise FeatureShadowError("feature shadow report content does not match its identity")
        score_candidates: list[str] = []
        for score_id in report.score_ids:
            score = self.load_score(score_id)
            if (
                score.registration_id != report.registration_id
                or score.model_version_id != report.model_version_id
                or score.purpose is not report.purpose
                or score.label_contract_id != report.label_contract_id
                or score.scored_at != report.scored_at
                or score.calibration_mode != report.calibration_mode
                or score.calibration_id != report.calibration_id
            ):
                raise FeatureShadowError("feature shadow report score evidence does not match")
            score_candidates.append(score.candidate_id)
        failure_candidates = [item.candidate_id for item in report.failures]
        if set(score_candidates) & set(failure_candidates):
            raise FeatureShadowError("feature shadow report candidate accounting overlaps")
        return report


def _is_sha256(value: str) -> bool:
    normalized = value.strip().lower()
    return len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized)


def _labels_for(purpose: TrainingDatasetPurpose) -> tuple[TrainingLabel, ...]:
    return {
        TrainingDatasetPurpose.CANDIDATE_SUCCESS: (
            TrainingLabel.NOT_SUCCESS,
            TrainingLabel.SUCCESS,
        ),
        TrainingDatasetPurpose.ANNOTATION_DECISION: (
            TrainingLabel.DISCARD,
            TrainingLabel.WATCH,
            TrainingLabel.PREPARE_BUY,
        ),
        TrainingDatasetPurpose.BUY_TIMING: (
            TrainingLabel.NOT_PURCHASED,
            TrainingLabel.PURCHASED,
        ),
        TrainingDatasetPurpose.SELL_TIMING: (TrainingLabel.NOT_SOLD, TrainingLabel.SOLD),
    }[purpose]


def _predicted_label(
    probabilities: Mapping[str, Decimal], labels: Sequence[TrainingLabel]
) -> TrainingLabel:
    return max(
        labels,
        key=lambda label: (probabilities[label.value], -labels.index(label)),
    )


def _report_id(report: FeatureShadowReport) -> str:
    identity = report.model_dump(mode="json")
    identity.pop("report_id", None)
    return f"feature-shadow-report-{sha256_json(identity)[:20]}"


def _safe_identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(
        character in normalized for character in ("/", "\\", "\0", "\r", "\n")
    ):
        raise FeatureShadowError("feature shadow identifier contains an invalid path character")
    return normalized


def _score_id(score: FeatureShadowScore) -> str:
    identity = score.model_dump(mode="json")
    identity.pop("score_id", None)
    return f"feature-shadow-score-{sha256_json(identity)[:20]}"


def _validate_probability_map(
    probabilities: Mapping[str, Decimal],
    labels: Sequence[TrainingLabel],
    name: str,
) -> None:
    if set(probabilities) != {label.value for label in labels}:
        raise ValueError(f"feature shadow {name} probabilities do not match classes")
    if any(not value.is_finite() or not 0 <= value <= 1 for value in probabilities.values()):
        raise ValueError(f"feature shadow {name} probabilities are invalid")
    if abs(sum(probabilities.values(), Decimal("0")) - Decimal("1")) > Decimal("1e-6"):
        raise ValueError(f"feature shadow {name} probabilities must sum to one")


def _write_once(path: Path, content: bytes) -> None:
    if path.exists():
        try:
            if path.read_bytes() != content:
                raise FeatureShadowError(
                    "feature shadow artifact identity already has different content"
                )
        except OSError as exc:
            raise FeatureShadowError("failed to read existing feature shadow artifact") from exc
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise FeatureShadowError("failed to write feature shadow artifact") from exc
