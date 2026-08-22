"""Train a feature-lineage-aware constant distribution baseline."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import cast

from stratgauge.ml._artifacts import write_immutable_bundle
from stratgauge.ml._hashing import sha256_bytes, sha256_json
from stratgauge.ml.training_datasets import (
    PredictionSplit,
    PreparedTrainingLabelDataset,
    SelectionTrainingLabelRow,
    TrainingLabelReadinessReport,
    TrainingLabelSplitManifest,
    audit_training_label_split,
    build_training_label_split_manifest,
    ordered_labels_for,
    read_training_label_dataset,
    split_training_label_dataset,
)
from stratgauge.ml.training_datasets import (
    TrainingSplitName as SplitName,
)
from stratgauge.ml.training_feature_baseline.helpers_b import (
    _metrics,
    _predictions,
)
from stratgauge.ml.training_feature_baseline.models import (
    FEATURE_BASELINE_MODEL_TYPE,
    FEATURE_BASELINE_REPORT_VERSION,
    FeatureBaselineEvaluationEvidence,
    FeatureBaselineModel,
    FeatureBaselineTrainingResult,
    TrainingFeatureSplitInput,
    _validate_cross_partition_sample_ids,
    _validate_feature_label_pairs,
    _validate_label_lineage,
)
from stratgauge.ml.training_feature_join import (
    TRAINING_FEATURE_DATASET_SCHEMA_VERSION,
    TRAINING_FEATURE_SPLIT_MANIFEST_VERSION,
    SelectionTrainingFeatureRow,
    TrainingFeatureJoinReport,
    TrainingFeatureSplitManifest,
    serialize_training_feature_dataset,
)


def read_training_feature_split(output_dir: str | Path) -> TrainingFeatureSplitInput:
    """Read feature partitions only if their manifest and join report agree."""

    directory = Path(output_dir)
    try:
        manifest_content = (directory / "manifest.json").read_bytes()
        manifest = TrainingFeatureSplitManifest.model_validate_json(manifest_content)
        report_content = (directory / "join-report.json").read_bytes()
        report = TrainingFeatureJoinReport.model_validate_json(report_content)
        label_split_manifest_content = (directory / "label-split-manifest.json").read_bytes()
        label_split_manifest = TrainingLabelSplitManifest.model_validate_json(
            label_split_manifest_content
        )
        label_readiness_content = (directory / "label-readiness.json").read_bytes()
        label_readiness = TrainingLabelReadinessReport.model_validate_json(label_readiness_content)
        partitions = {
            name: _read_feature_dataset((directory / f"{name}.jsonl").read_bytes())
            for name in ("train", "validation", "test")
        }
        label_partitions = {
            name: read_training_label_dataset((directory / f"label-{name}.jsonl").read_bytes())
            for name in ("train", "validation", "test")
        }
        purged_content = (directory / "purged-labels.jsonl").read_bytes()
        purged_rows = read_training_label_dataset(purged_content)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid training feature split: {directory}: {exc}") from exc
    _validate_manifest(
        manifest,
        report,
        label_split_manifest,
        label_readiness,
        partitions,
        label_partitions,
        purged_rows,
        purged_content,
        report_content,
        label_split_manifest_content,
        label_readiness_content,
    )
    return TrainingFeatureSplitInput(
        manifest=manifest,
        report=report,
        label_split_manifest=label_split_manifest,
        label_readiness=label_readiness,
        label_train_rows=label_partitions["train"],
        label_validation_rows=label_partitions["validation"],
        label_test_rows=label_partitions["test"],
        purged_rows=purged_rows,
        train_rows=partitions["train"],
        validation_rows=partitions["validation"],
        test_rows=partitions["test"],
        manifest_sha256=sha256_bytes(manifest_content),
    )


def train_constant_feature_baseline(
    split_input: TrainingFeatureSplitInput,
    *,
    random_seed: int = 0,
    minimum_labeled_samples: int = 100,
) -> FeatureBaselineTrainingResult:
    """Fit only train class priors after verifying complete feature coverage."""

    if minimum_labeled_samples <= 0:
        raise ValueError("minimum_labeled_samples must be positive")
    if split_input.report.failure_count:
        raise ValueError("feature join contains failures; incomplete coverage cannot train")
    train_rows = split_input.train_rows
    if not train_rows:
        raise ValueError("feature training split contains no rows")
    if not split_input.validation_rows:
        raise ValueError("feature validation split contains no rows")
    all_rows = train_rows + split_input.validation_rows + split_input.test_rows
    if len(all_rows) < minimum_labeled_samples:
        raise ValueError(
            f"feature labels {len(all_rows)} are below minimum {minimum_labeled_samples}"
        )
    if len(train_rows) != split_input.manifest.label_row_counts["train"]:
        raise ValueError("feature train rows do not match label lineage")
    if len(split_input.validation_rows) != split_input.manifest.label_row_counts["validation"]:
        raise ValueError("feature validation rows do not match label lineage")
    if len(split_input.test_rows) != split_input.manifest.label_row_counts["test"]:
        raise ValueError("feature test rows do not match label lineage")
    labels = ordered_labels_for(split_input.manifest.purpose)
    counts = Counter(row.target for row in train_rows)
    if len([label for label in labels if counts[label]]) < 2:
        raise ValueError("feature training labels must contain at least two classes")
    probabilities = {
        label.value: Decimal(counts[label]) / Decimal(len(train_rows)) for label in labels
    }
    identity = {
        "feature_dataset_manifest_sha256": split_input.manifest_sha256,
        "feature_names": split_input.manifest.feature_names,
        "feature_set_version_id": split_input.manifest.feature_set_version_id,
        "label_contract_id": split_input.manifest.label_contract_id,
        "label_split_manifest_sha256": split_input.manifest.label_split_manifest_sha256,
        "model_type": FEATURE_BASELINE_MODEL_TYPE,
        "probabilities": {name: str(value) for name, value in probabilities.items()},
        "purpose": split_input.manifest.purpose.value,
        "random_seed": random_seed,
        "source_label_dataset_sha256": split_input.manifest.source_label_dataset_sha256,
    }
    model = FeatureBaselineModel(
        model_version_id=f"feature-label-baseline-{sha256_json(identity)[:20]}",
        purpose=split_input.manifest.purpose,
        label_contract_id=split_input.manifest.label_contract_id,
        feature_set_version_id=split_input.manifest.feature_set_version_id,
        feature_names=split_input.manifest.feature_names,
        class_labels=labels,
        probabilities=probabilities,
        train_row_count=len(train_rows),
        train_target_counts={label.value: counts[label] for label in labels},
        random_seed=random_seed,
        minimum_labeled_samples=minimum_labeled_samples,
        feature_dataset_manifest_sha256=split_input.manifest_sha256,
        source_label_dataset_sha256=split_input.manifest.source_label_dataset_sha256,
        label_split_manifest_sha256=split_input.manifest.label_split_manifest_sha256,
        row_counts=split_input.manifest.row_counts,
    )
    metrics = tuple(
        _metrics(cast(SplitName, name), rows, probabilities, labels)
        for name, rows in (
            ("train", split_input.train_rows),
            ("validation", split_input.validation_rows),
            ("test", split_input.test_rows),
        )
    )
    predictions = tuple(
        prediction
        for name, rows in (
            ("validation", split_input.validation_rows),
            ("test", split_input.test_rows),
        )
        for prediction in _predictions(cast(PredictionSplit, name), rows, probabilities, labels)
    )
    return FeatureBaselineTrainingResult(model=model, metrics=metrics, predictions=predictions)


def write_constant_feature_baseline(
    output_dir: str | Path,
    result: FeatureBaselineTrainingResult,
) -> None:
    """Write immutable model, evaluation, predictions and manifest evidence."""

    destination = Path(output_dir)
    outputs = {
        "model.json": result.model.model_dump_json(indent=2) + "\n",
        "metrics.json": FeatureBaselineEvaluationEvidence(
            model_version_id=result.model.model_version_id,
            purpose=result.model.purpose,
            label_contract_id=result.model.label_contract_id,
            feature_set_version_id=result.model.feature_set_version_id,
            feature_dataset_manifest_sha256=result.model.feature_dataset_manifest_sha256,
            metrics=result.metrics,
        ).model_dump_json(indent=2)
        + "\n",
        "predictions.jsonl": "".join(
            f"{prediction.model_dump_json()}\n" for prediction in result.predictions
        ),
        "manifest.json": json.dumps(
            {
                "report_version": FEATURE_BASELINE_REPORT_VERSION,
                "model_type": FEATURE_BASELINE_MODEL_TYPE,
                "purpose": result.model.purpose.value,
                "label_contract_id": result.model.label_contract_id,
                "feature_set_version_id": result.model.feature_set_version_id,
                "feature_names": result.model.feature_names,
                "feature_dataset_manifest_sha256": result.model.feature_dataset_manifest_sha256,
                "source_label_dataset_sha256": result.model.source_label_dataset_sha256,
                "label_split_manifest_sha256": result.model.label_split_manifest_sha256,
                "training_parameters": {
                    "random_seed": result.model.random_seed,
                    "minimum_labeled_samples": result.model.minimum_labeled_samples,
                    "brier_score": "multiclass squared error divided by 2",
                },
                "row_counts": result.model.row_counts,
                "model_version_id": result.model.model_version_id,
                "purged_rows_excluded": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }
    write_immutable_bundle(
        destination,
        outputs,
        already_exists_message=f"feature baseline output already exists: {destination}",
    )


def _read_feature_dataset(content: bytes) -> tuple[SelectionTrainingFeatureRow, ...]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("training feature dataset is not valid UTF-8") from exc
    rows: list[SelectionTrainingFeatureRow] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("training feature row must be a JSON object")
            if payload.get("schema_version") != TRAINING_FEATURE_DATASET_SCHEMA_VERSION:
                raise ValueError("unsupported training feature dataset schema")
            rows.append(SelectionTrainingFeatureRow.model_validate(payload))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid training feature row at line {number}: {exc}") from exc
    return tuple(rows)


def _validate_manifest(
    manifest: TrainingFeatureSplitManifest,
    report: TrainingFeatureJoinReport,
    label_split_manifest: TrainingLabelSplitManifest,
    label_readiness: TrainingLabelReadinessReport,
    partitions: dict[str, tuple[SelectionTrainingFeatureRow, ...]],
    label_partitions: dict[str, tuple[SelectionTrainingLabelRow, ...]],
    purged_rows: tuple[SelectionTrainingLabelRow, ...],
    purged_content: bytes,
    report_content: bytes,
    label_split_manifest_content: bytes,
    label_readiness_content: bytes,
) -> None:
    if manifest.manifest_version != TRAINING_FEATURE_SPLIT_MANIFEST_VERSION:
        raise ValueError("unsupported training feature split manifest version")
    if manifest.dataset_schema_version != TRAINING_FEATURE_DATASET_SCHEMA_VERSION:
        raise ValueError("training feature split has unsupported dataset schema")
    if (
        manifest.purpose is not report.purpose
        or manifest.label_contract_id != report.label_contract_id
    ):
        raise ValueError("training feature manifest purpose does not match join report")
    if manifest.feature_set_version_id != report.feature_set_version_id:
        raise ValueError("training feature manifest feature set does not match join report")
    if manifest.source_label_dataset_sha256 != report.source_label_dataset_sha256:
        raise ValueError("training feature source label hash does not match join report")
    if label_split_manifest.purpose is not manifest.purpose:
        raise ValueError("training feature label manifest purpose does not match")
    if label_split_manifest.label_contract_id != manifest.label_contract_id:
        raise ValueError("training feature label manifest contract does not match")
    if label_readiness.purpose is not manifest.purpose:
        raise ValueError("training feature readiness purpose does not match")
    if label_readiness.label_contract_id != manifest.label_contract_id:
        raise ValueError("training feature readiness contract does not match")
    if manifest.label_split_manifest_sha256 != sha256_bytes(label_split_manifest_content):
        raise ValueError("training feature manifest hash does not match label split manifest")
    if manifest.label_readiness_sha256 != sha256_bytes(label_readiness_content):
        raise ValueError("training feature manifest hash does not match label readiness")
    expected_contents = {
        name: serialize_training_feature_dataset(rows).encode("utf-8")
        for name, rows in partitions.items()
    }
    expected_contents["purged"] = purged_content
    expected_label_contents = {
        name: _serialize_label_rows(rows).encode("utf-8") for name, rows in label_partitions.items()
    }
    expected_label_contents["purged"] = _serialize_label_rows(purged_rows).encode("utf-8")
    if set(manifest.row_counts) != {"train", "validation", "test", "purged"}:
        raise ValueError("training feature manifest row counts are incomplete")
    if set(manifest.partition_sha256) != set(manifest.row_counts):
        raise ValueError("training feature manifest partition hashes are incomplete")
    if set(manifest.label_row_counts) != set(manifest.row_counts):
        raise ValueError("training feature manifest label row counts are incomplete")
    if set(manifest.label_partition_sha256) != set(manifest.row_counts):
        raise ValueError("training feature manifest label partition hashes are incomplete")
    for name, content in expected_contents.items():
        if manifest.row_counts[name] != len(content.splitlines()):
            raise ValueError(f"training feature manifest row count does not match {name}")
        if manifest.partition_sha256[name] != sha256_bytes(content):
            raise ValueError(f"training feature manifest hash does not match {name}")
        label_content = expected_label_contents[name]
        if manifest.label_row_counts[name] != len(label_content.splitlines()):
            raise ValueError(f"training feature label row count does not match {name}")
        if manifest.label_partition_sha256[name] != sha256_bytes(label_content):
            raise ValueError(f"training feature label hash does not match {name}")
    if manifest.join_report_sha256 != sha256_bytes(report_content):
        raise ValueError("training feature manifest hash does not match join report")
    if manifest.prepared_label_dataset_sha256 != label_readiness.prepared_dataset_sha256:
        raise ValueError("training feature prepared label hash does not match readiness")
    _validate_label_lineage(
        label_partitions["train"],
        label_partitions["validation"],
        label_partitions["test"],
        purged_rows,
        manifest,
    )
    _validate_label_split_evidence(
        manifest,
        report,
        label_split_manifest,
        label_readiness,
        label_partitions,
        purged_rows,
    )
    all_rows = partitions["train"] + partitions["validation"] + partitions["test"]
    _validate_cross_partition_sample_ids(all_rows)
    feature_names = tuple(sorted({name for row in all_rows for name in row.features}))
    if feature_names != manifest.feature_names:
        raise ValueError("training feature manifest names do not match feature rows")
    _validate_feature_label_pairs(partitions["train"], label_partitions["train"], "train")
    _validate_feature_label_pairs(
        partitions["validation"], label_partitions["validation"], "validation"
    )
    _validate_feature_label_pairs(partitions["test"], label_partitions["test"], "test")


def _serialize_label_rows(rows: Sequence[SelectionTrainingLabelRow]) -> str:
    return "".join(f"{row.model_dump_json()}\n" for row in rows)


def _validate_label_split_evidence(
    manifest: TrainingFeatureSplitManifest,
    report: TrainingFeatureJoinReport,
    label_split_manifest: TrainingLabelSplitManifest,
    label_readiness: TrainingLabelReadinessReport,
    label_partitions: dict[str, tuple[SelectionTrainingLabelRow, ...]],
    purged_rows: tuple[SelectionTrainingLabelRow, ...],
) -> None:
    label_rows = tuple(
        sorted(
            (
                *label_partitions["train"],
                *label_partitions["validation"],
                *label_partitions["test"],
                *purged_rows,
            ),
            key=lambda row: (row.observation_date, row.sample_id),
        )
    )
    prepared = PreparedTrainingLabelDataset(
        purpose=label_split_manifest.purpose,
        label_contract_id=label_split_manifest.label_contract_id,
        source_schema_version=label_split_manifest.source_schema_version,
        source_file_sha256=label_split_manifest.source_file_sha256,
        source_row_count=label_split_manifest.source_row_count,
        labeled_row_count=len(label_rows),
        skipped_unlabeled_row_count=label_split_manifest.skipped_unlabeled_row_count,
        filtered_policy_row_count=label_split_manifest.filtered_policy_row_count,
        tracking_policy_id=label_split_manifest.tracking_policy_id,
        outcome_policy_id=label_split_manifest.outcome_policy_id,
        window_sessions=label_split_manifest.window_sessions,
        rows=label_rows,
    )
    expected_split = split_training_label_dataset(
        prepared,
        train_end=label_split_manifest.train_end,
        validation_end=label_split_manifest.validation_end,
        label_available_through=label_split_manifest.label_available_through,
    )
    if expected_split.train_rows != label_partitions["train"]:
        raise ValueError("training feature label train partition does not match split manifest")
    if expected_split.validation_rows != label_partitions["validation"]:
        raise ValueError(
            "training feature label validation partition does not match split manifest"
        )
    if expected_split.test_rows != label_partitions["test"]:
        raise ValueError("training feature label test partition does not match split manifest")
    if expected_split.purged_rows != purged_rows:
        raise ValueError("training feature purged labels do not match split manifest")
    if build_training_label_split_manifest(expected_split) != label_split_manifest:
        raise ValueError("training feature label split manifest does not match label rows")
    if (
        audit_training_label_split(
            expected_split,
            minimum_labeled_samples=label_readiness.minimum_labeled_samples,
        )
        != label_readiness
    ):
        raise ValueError("training feature label readiness does not match label rows")
    if manifest.source_label_dataset_sha256 != report.source_label_dataset_sha256:
        raise ValueError("training feature source label hash does not match join report")
