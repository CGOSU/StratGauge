"""Explicit application workflows for the purpose-specific ML training chain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from stratgauge.features.store import FeatureSnapshotStore
from stratgauge.ml.training_datasets import (
    PreparedTrainingLabelDataset,
    TrainingDatasetPurpose,
    TrainingLabelDatasetSplit,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    audit_training_label_split,
    prepare_training_label_dataset,
    split_training_label_dataset,
    write_training_label_split,
)
from stratgauge.ml.training_feature_baseline import (
    FeatureBaselineTrainingResult,
    read_training_feature_split,
    train_constant_feature_baseline,
    write_constant_feature_baseline,
)
from stratgauge.ml.training_feature_join import (
    TrainingFeatureDatasetSplit,
    join_training_features,
    write_training_feature_split,
)
from stratgauge.ml.training_feature_lightgbm import (
    FeatureLightGBMTrainingResult,
    FeatureLightGBMUnavailableError,
    train_feature_lightgbm,
    write_feature_lightgbm_result,
)
from stratgauge.ml.training_label_baseline import (
    LabelBaselineTrainingResult,
    read_training_label_split,
    train_constant_label_baseline,
    write_constant_label_baseline,
)


@dataclass(frozen=True)
class PrepareTrainingDatasetRequest:
    """Inputs for one manually invoked label preparation stage."""

    input_path: Path
    purpose: TrainingDatasetPurpose
    output_dir: Path
    train_end: date
    validation_end: date
    label_available_through: datetime
    minimum_labeled_samples: int = 100
    tracking_policy_id: str | None = None


@dataclass(frozen=True)
class PreparedTrainingDatasetResult:
    """Evidence returned after writing one prepared label split."""

    output_dir: Path
    prepared: PreparedTrainingLabelDataset
    split: TrainingLabelDatasetSplit
    report: TrainingLabelReadinessReport
    manifest: TrainingLabelSplitManifest


@dataclass(frozen=True)
class JoinTrainingFeaturesRequest:
    """Inputs for one manually invoked point-in-time feature join stage."""

    labels_dir: Path
    feature_root: Path
    feature_set_version_id: str
    feature_snapshot_ids: tuple[str, ...]
    output_dir: Path


@dataclass(frozen=True)
class JoinedTrainingFeaturesResult:
    """Evidence returned after writing one joined feature split."""

    output_dir: Path
    split: TrainingFeatureDatasetSplit


@dataclass(frozen=True)
class TrainLabelBaselineRequest:
    """Inputs for one manually invoked label baseline stage."""

    input_dir: Path
    output_dir: Path
    random_seed: int = 0
    minimum_labeled_samples: int = 100


@dataclass(frozen=True)
class LabelBaselineResult:
    """Evidence returned after writing one label baseline."""

    output_dir: Path
    result: LabelBaselineTrainingResult


@dataclass(frozen=True)
class TrainFeatureBaselineRequest:
    """Inputs for one manually invoked feature baseline stage."""

    input_dir: Path
    output_dir: Path
    random_seed: int = 0
    minimum_labeled_samples: int = 100


@dataclass(frozen=True)
class FeatureBaselineResult:
    """Evidence returned after writing one feature baseline."""

    output_dir: Path
    result: FeatureBaselineTrainingResult


@dataclass(frozen=True)
class TrainFeatureLightGBMRequest:
    """Inputs for one manually invoked optional LightGBM stage."""

    input_dir: Path
    output_dir: Path
    random_seed: int = 0
    minimum_labeled_samples: int = 100


@dataclass(frozen=True)
class FeatureLightGBMResult:
    """Evidence returned after writing one LightGBM result."""

    output_dir: Path
    result: FeatureLightGBMTrainingResult


def run_prepare_training_dataset(
    request: PrepareTrainingDatasetRequest,
) -> PreparedTrainingDatasetResult:
    """Prepare, split, audit and write one label dataset stage."""

    prepared = prepare_training_label_dataset(
        request.input_path.read_bytes(),
        purpose=request.purpose,
        tracking_policy_id=request.tracking_policy_id,
    )
    split = split_training_label_dataset(
        prepared,
        train_end=request.train_end,
        validation_end=request.validation_end,
        label_available_through=request.label_available_through,
    )
    report = audit_training_label_split(
        split,
        minimum_labeled_samples=request.minimum_labeled_samples,
    )
    manifest = write_training_label_split(request.output_dir, split, report)
    return PreparedTrainingDatasetResult(
        output_dir=request.output_dir,
        prepared=prepared,
        split=split,
        report=report,
        manifest=manifest,
    )


def run_join_training_features(
    request: JoinTrainingFeaturesRequest,
) -> JoinedTrainingFeaturesResult:
    """Read labels, join explicitly supplied snapshots and write one feature stage."""

    label_input = read_training_label_split(request.labels_dir)
    snapshot_ids = tuple(value.strip() for value in request.feature_snapshot_ids if value.strip())
    split = join_training_features(
        label_input.split,
        feature_store=FeatureSnapshotStore(request.feature_root),
        feature_snapshot_ids=snapshot_ids,
        feature_set_version_id=request.feature_set_version_id,
        label_split_manifest=label_input.manifest,
        label_readiness=label_input.readiness,
    )
    write_training_feature_split(request.output_dir, split)
    return JoinedTrainingFeaturesResult(output_dir=request.output_dir, split=split)


def run_train_label_baseline(request: TrainLabelBaselineRequest) -> LabelBaselineResult:
    """Read a label split, train one baseline and write its evidence."""

    split_input = read_training_label_split(request.input_dir)
    result = train_constant_label_baseline(
        split_input,
        random_seed=request.random_seed,
        minimum_labeled_samples=request.minimum_labeled_samples,
    )
    write_constant_label_baseline(request.output_dir, result)
    return LabelBaselineResult(output_dir=request.output_dir, result=result)


def run_train_feature_baseline(request: TrainFeatureBaselineRequest) -> FeatureBaselineResult:
    """Read a feature split, train one baseline and write its evidence."""

    split_input = read_training_feature_split(request.input_dir)
    result = train_constant_feature_baseline(
        split_input,
        random_seed=request.random_seed,
        minimum_labeled_samples=request.minimum_labeled_samples,
    )
    write_constant_feature_baseline(request.output_dir, result)
    return FeatureBaselineResult(output_dir=request.output_dir, result=result)


def run_train_feature_lightgbm(
    request: TrainFeatureLightGBMRequest,
) -> FeatureLightGBMResult:
    """Read a feature split, train one LightGBM model and write its evidence."""

    split_input = read_training_feature_split(request.input_dir)
    result = train_feature_lightgbm(
        split_input,
        random_seed=request.random_seed,
        minimum_labeled_samples=request.minimum_labeled_samples,
    )
    write_feature_lightgbm_result(request.output_dir, result)
    return FeatureLightGBMResult(output_dir=request.output_dir, result=result)


__all__ = [
    "FeatureBaselineResult",
    "FeatureLightGBMResult",
    "FeatureLightGBMUnavailableError",
    "JoinTrainingFeaturesRequest",
    "JoinedTrainingFeaturesResult",
    "LabelBaselineResult",
    "PrepareTrainingDatasetRequest",
    "PreparedTrainingDatasetResult",
    "TrainFeatureBaselineRequest",
    "TrainFeatureLightGBMRequest",
    "TrainLabelBaselineRequest",
    "run_join_training_features",
    "run_prepare_training_dataset",
    "run_train_feature_baseline",
    "run_train_feature_lightgbm",
    "run_train_label_baseline",
]
