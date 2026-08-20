"""Read-only machine-learning readiness for candidate-success labels."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from stratgauge.backtest.selection_outcomes import latest_selection_outcomes
from stratgauge.data_pipeline.selection_store import SelectionImportStore, SelectionStoreError
from stratgauge.data_pipeline.store import SnapshotManifest, SnapshotStatus
from stratgauge.data_sources.base import (
    INDEX_DAILY_BENCHMARK_SYMBOL,
    DailyBarRequest,
)
from stratgauge.domain.models import DomainModel
from stratgauge.domain.selections import SelectionOutcomeStatus
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.ml.training_datasets import TrainingDatasetPurpose
from stratgauge.ml.training_feature_registry import (
    FeatureModelLifecycleEvent,
    FeatureModelRegistration,
)

ML_READINESS_REPORT_VERSION: Literal["selection-ml-readiness-v1"] = "selection-ml-readiness-v1"
MINIMUM_CANDIDATE_SUCCESS_SAMPLES = 100

MlReadinessStage = Literal[
    "collecting_labels",
    "awaiting_feature_evidence",
    "ready_for_training",
    "model_registered",
    "shadow_ready",
]


class MLReadinessError(RuntimeError):
    """The local ML readiness evidence cannot be read safely."""


class MLReadinessReport(DomainModel):
    """A non-mutating, operator-facing ML readiness assessment."""

    report_version: Literal["selection-ml-readiness-v1"] = ML_READINESS_REPORT_VERSION
    purpose: Literal["candidate_success"] = "candidate_success"
    stage: MlReadinessStage
    minimum_labeled_samples: int = Field(gt=0)
    source_batch_count: int = Field(ge=0)
    source_candidate_count: int = Field(ge=0)
    accepted_label_count: int = Field(ge=0)
    label_shortfall: int = Field(ge=0)
    target_counts: dict[str, int]
    outcome_counts: dict[str, int]
    tracking_policy_ids: tuple[str, ...]
    outcome_policy_ids: tuple[str, ...]
    feature_snapshot_count: int = Field(ge=0)
    invalid_feature_snapshot_count: int = Field(ge=0)
    benchmark_snapshot_count: int = Field(ge=0)
    adjustment_factor_snapshot_count: int = Field(ge=0)
    invalid_market_manifest_count: int = Field(ge=0)
    registered_model_count: int = Field(ge=0)
    calibrated_model_count: int = Field(ge=0)
    shadow_model_count: int = Field(ge=0)
    invalid_model_evidence_count: int = Field(ge=0)
    label_ready: bool
    evidence_ready: bool
    training_ready: bool
    blockers: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SelectionEvidence:
    batch_count: int
    candidate_count: int
    accepted_label_count: int
    target_counts: dict[str, int]
    outcome_counts: dict[str, int]
    tracking_policy_ids: tuple[str, ...]
    outcome_policy_ids: tuple[str, ...]


@dataclass(frozen=True)
class _MarketEvidence:
    benchmark_snapshot_count: int
    adjustment_factor_snapshot_count: int
    invalid_manifest_count: int


@dataclass(frozen=True)
class _FeatureEvidence:
    snapshot_count: int
    invalid_snapshot_count: int


@dataclass(frozen=True)
class _ModelEvidence:
    registered_count: int
    calibrated_count: int
    shadow_count: int
    invalid_count: int


class MLReadinessService:
    """Inspect immutable local evidence without creating or changing any files."""

    def __init__(
        self,
        selection_store: SelectionImportStore,
        *,
        data_root: str | Path,
        market_root: str | Path,
        model_root: str | Path,
        minimum_labeled_samples: int = MINIMUM_CANDIDATE_SUCCESS_SAMPLES,
    ) -> None:
        if minimum_labeled_samples <= 0:
            raise ValueError("minimum_labeled_samples must be positive")
        self._selection_store = selection_store
        self._data_root = Path(data_root)
        self._market_root = Path(market_root)
        self._model_root = Path(model_root)
        self._minimum_labeled_samples = minimum_labeled_samples

    def get_readiness(self) -> MLReadinessReport:
        """Return the current training and shadow evidence gates."""

        try:
            selection = self._read_selection_evidence()
            market = self._read_market_evidence()
            features = self._read_feature_evidence()
            models = self._read_model_evidence()
        except MLReadinessError:
            raise
        except SelectionStoreError as exc:
            raise MLReadinessError("选股事实读取失败，无法计算机器学习就绪状态。") from exc
        except (OSError, TypeError, ValueError) as exc:
            raise MLReadinessError("机器学习就绪证据读取失败。") from exc

        label_ready = self._labels_are_ready(selection)
        evidence_ready = self._features_are_ready(selection, market, features)
        training_ready = label_ready and evidence_ready
        stage = _stage_for(label_ready, evidence_ready, models)
        blockers = self._blockers(selection, market, features, models, label_ready, evidence_ready)
        next_steps = self._next_steps(
            selection,
            market,
            features,
            models,
            label_ready,
            evidence_ready,
            training_ready,
        )
        return MLReadinessReport(
            stage=stage,
            minimum_labeled_samples=self._minimum_labeled_samples,
            source_batch_count=selection.batch_count,
            source_candidate_count=selection.candidate_count,
            accepted_label_count=selection.accepted_label_count,
            label_shortfall=max(
                0,
                self._minimum_labeled_samples - selection.accepted_label_count,
            ),
            target_counts=selection.target_counts,
            outcome_counts=selection.outcome_counts,
            tracking_policy_ids=selection.tracking_policy_ids,
            outcome_policy_ids=selection.outcome_policy_ids,
            feature_snapshot_count=features.snapshot_count,
            invalid_feature_snapshot_count=features.invalid_snapshot_count,
            benchmark_snapshot_count=market.benchmark_snapshot_count,
            adjustment_factor_snapshot_count=market.adjustment_factor_snapshot_count,
            invalid_market_manifest_count=market.invalid_manifest_count,
            registered_model_count=models.registered_count,
            calibrated_model_count=models.calibrated_count,
            shadow_model_count=models.shadow_count,
            invalid_model_evidence_count=models.invalid_count,
            label_ready=label_ready,
            evidence_ready=evidence_ready,
            training_ready=training_ready,
            blockers=tuple(dict.fromkeys(blockers)),
            next_steps=tuple(dict.fromkeys(next_steps)),
        )

    def _read_selection_evidence(self) -> _SelectionEvidence:
        manifests = self._selection_store.list_manifests(include_archived=True)
        imports = tuple(self._selection_store.load_import(item.batch_id) for item in manifests)
        candidate_ids = tuple(
            candidate.candidate_id for imported in imports for candidate in imported.candidates
        )
        outcomes_by_candidate = self._selection_store.list_outcomes_for_candidates(candidate_ids)
        target_counts = Counter({"success": 0, "not_success": 0})
        outcome_counts = Counter({status.value: 0 for status in SelectionOutcomeStatus})
        outcome_counts["pending"] = 0
        tracking_policy_ids: set[str] = set()
        outcome_policy_ids: set[str] = set()
        accepted_label_count = 0
        for candidate_id in candidate_ids:
            latest = latest_selection_outcomes(outcomes_by_candidate[candidate_id])
            if not latest:
                outcome_counts["pending"] += 1
                continue
            for outcome in latest:
                outcome_counts[outcome.status.value] += 1
                if outcome.status not in {
                    SelectionOutcomeStatus.WIN,
                    SelectionOutcomeStatus.LOSS,
                    SelectionOutcomeStatus.FLAT,
                }:
                    continue
                accepted_label_count += 1
                target_counts[
                    "success" if outcome.status is SelectionOutcomeStatus.WIN else "not_success"
                ] += 1
                tracking_policy_ids.add(outcome.tracking_policy_id)
                outcome_policy_ids.add(outcome.outcome_policy_id)
        return _SelectionEvidence(
            batch_count=len(imports),
            candidate_count=len(candidate_ids),
            accepted_label_count=accepted_label_count,
            target_counts=dict(sorted(target_counts.items())),
            outcome_counts=dict(sorted(outcome_counts.items())),
            tracking_policy_ids=tuple(sorted(tracking_policy_ids)),
            outcome_policy_ids=tuple(sorted(outcome_policy_ids)),
        )

    def _read_market_evidence(self) -> _MarketEvidence:
        manifest_dir = self._market_root / "manifests"
        if not manifest_dir.exists():
            return _MarketEvidence(0, 0, 0)
        benchmark_count = 0
        factor_count = 0
        invalid_count = 0
        for path in sorted(manifest_dir.glob("*.json")):
            try:
                manifest = SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                invalid_count += 1
                continue
            if manifest.status is not SnapshotStatus.APPROVED:
                continue
            if manifest.request_kind == "index_daily":
                symbol = _read_index_daily_symbol(manifest, self._market_root)
                if symbol is None:
                    invalid_count += 1
                elif symbol == INDEX_DAILY_BENCHMARK_SYMBOL:
                    benchmark_count += 1
            elif manifest.request_kind == "adjustment_factor":
                factor_count += 1
        return _MarketEvidence(benchmark_count, factor_count, invalid_count)

    def _read_feature_evidence(self) -> _FeatureEvidence:
        snapshot_dir = self._data_root / "features" / "snapshots"
        if not snapshot_dir.exists():
            return _FeatureEvidence(0, 0)
        snapshot_count = 0
        invalid_count = 0
        for path in sorted(snapshot_dir.glob("feature-snapshot-*.json")):
            try:
                FeatureSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                invalid_count += 1
                continue
            snapshot_count += 1
        return _FeatureEvidence(snapshot_count, invalid_count)

    def _read_model_evidence(self) -> _ModelEvidence:
        registration_dir = self._model_root / "models" / "feature-registry" / "registrations"
        event_dir = self._model_root / "models" / "feature-registry" / "lifecycle"
        if not registration_dir.exists():
            return _ModelEvidence(0, 0, 0, 0)
        events: dict[str, list[FeatureModelLifecycleEvent]] = {}
        invalid_count = 0
        for path in sorted(event_dir.glob("*.json")) if event_dir.exists() else ():
            try:
                event = FeatureModelLifecycleEvent.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, TypeError, ValueError):
                invalid_count += 1
                continue
            events.setdefault(event.registration_id, []).append(event)

        registered_count = 0
        calibrated_count = 0
        shadow_count = 0
        for path in sorted(registration_dir.glob("*.json")):
            try:
                registration = FeatureModelRegistration.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if registration.purpose is not TrainingDatasetPurpose.CANDIDATE_SUCCESS:
                    continue
                lifecycle = _current_lifecycle(
                    registration.registration_id,
                    events.get(registration.registration_id, ()),
                )
            except (OSError, TypeError, ValueError, MLReadinessError):
                invalid_count += 1
                continue
            registered_count += 1
            calibrated_count += int(registration.calibrated)
            shadow_count += int(lifecycle == "shadow")
        return _ModelEvidence(
            registered_count,
            calibrated_count,
            shadow_count,
            invalid_count,
        )

    def _labels_are_ready(self, evidence: _SelectionEvidence) -> bool:
        return (
            evidence.accepted_label_count >= self._minimum_labeled_samples
            and len(evidence.tracking_policy_ids) == 1
            and len(evidence.outcome_policy_ids) == 1
            and all(evidence.target_counts[label] > 0 for label in ("success", "not_success"))
        )

    @staticmethod
    def _features_are_ready(
        selection: _SelectionEvidence,
        market: _MarketEvidence,
        features: _FeatureEvidence,
    ) -> bool:
        return (
            selection.accepted_label_count > 0
            and features.invalid_snapshot_count == 0
            and features.snapshot_count >= selection.accepted_label_count
            and market.invalid_manifest_count == 0
            and market.benchmark_snapshot_count > 0
            and market.adjustment_factor_snapshot_count > 0
        )

    def _blockers(
        self,
        selection: _SelectionEvidence,
        market: _MarketEvidence,
        features: _FeatureEvidence,
        models: _ModelEvidence,
        label_ready: bool,
        evidence_ready: bool,
    ) -> list[str]:
        blockers: list[str] = []
        if selection.accepted_label_count < self._minimum_labeled_samples:
            blockers.append(
                f"已成熟候选结果不足：当前 {selection.accepted_label_count} 条，还需要 "
                f"{self._minimum_labeled_samples - selection.accepted_label_count} 条。"
            )
        if selection.accepted_label_count and (
            selection.target_counts["success"] == 0 or selection.target_counts["not_success"] == 0
        ):
            blockers.append("已成熟结果目前只有一个训练类别，需要同时出现 success 和 not_success。")
        if len(selection.tracking_policy_ids) > 1 or len(selection.outcome_policy_ids) > 1:
            blockers.append("训练数据包含多个结果政策，需明确固定一个 tracking/outcome policy。")
        if features.invalid_snapshot_count:
            blockers.append(f"存在 {features.invalid_snapshot_count} 个无法读取的特征快照。")
        if (
            selection.accepted_label_count
            and features.snapshot_count < selection.accepted_label_count
        ):
            blockers.append(
                f"特征快照不足：当前 {features.snapshot_count} 个，至少需要覆盖 "
                f"{selection.accepted_label_count} 个已成熟样本。"
            )
        if market.invalid_manifest_count:
            blockers.append(f"存在 {market.invalid_manifest_count} 个无法读取的行情清单。")
        if market.benchmark_snapshot_count == 0:
            blockers.append("缺少已批准的 000300.SH 独立 index_daily 基准证据。")
        if market.adjustment_factor_snapshot_count == 0:
            blockers.append("缺少候选股票已批准复权因子证据。")
        if models.invalid_count:
            blockers.append(f"模型注册目录存在 {models.invalid_count} 个无效证据文件。")
        if label_ready and not evidence_ready:
            blockers.append("标签门槛已满足，但特征、基准或复权因子证据尚未完整。")
        return blockers

    def _next_steps(
        self,
        selection: _SelectionEvidence,
        market: _MarketEvidence,
        features: _FeatureEvidence,
        models: _ModelEvidence,
        label_ready: bool,
        evidence_ready: bool,
        training_ready: bool,
    ) -> list[str]:
        steps: list[str] = []
        if selection.accepted_label_count < self._minimum_labeled_samples:
            steps.append("继续同步并运行成熟任务，直到 win/loss/flat 的已成熟结果达到 100 条。")
        if label_ready and len(selection.tracking_policy_ids) == 1:
            steps.append("保持候选成功率训练使用当前唯一的 tracking/outcome policy。")
        if features.snapshot_count < selection.accepted_label_count:
            steps.append("为已成熟样本生成点时特征快照，并保留每个输入快照的血缘。")
        if market.benchmark_snapshot_count == 0:
            steps.append(
                "先取得并批准 000300.SH 的独立 index_daily 快照；"
                "不能用股票 adj_factor 代替指数复权。"
            )
        if market.adjustment_factor_snapshot_count == 0:
            steps.append("为候选股票补齐并批准与日线同源、同股票池绑定的复权因子。")
        if training_ready and models.registered_count == 0:
            steps.append(
                "全部前置就绪后，显式运行 prepare → split → join → "
                "train_selection_feature_lightgbm.py（uv --extra ml）流水线。"
            )
        if models.registered_count and models.shadow_count == 0:
            steps.append(
                "训练后先注册 challenger，再由人工执行 transition 到 shadow；不要自动选择模型。"
            )
        if models.shadow_count:
            steps.append(
                "已有 shadow 注册；handoff 仍须固定 registration_id，并且评分永不用于决策。"
            )
        if not steps:
            steps.append("当前证据已满足就绪门，可查看 CLI 文档执行下一步人工机器学习流水线。")
        return steps


def _read_index_daily_symbol(manifest: SnapshotManifest, market_root: Path) -> str | None:
    try:
        payload = json.loads((market_root / manifest.raw_path).read_text(encoding="utf-8"))
        request = DailyBarRequest.model_validate(payload["request"])
    except (KeyError, OSError, TypeError, ValueError):
        return None
    if request.start_date != manifest.start_date or request.end_date != manifest.end_date:
        return None
    return request.security.symbol


def _stage_for(
    label_ready: bool,
    evidence_ready: bool,
    models: _ModelEvidence,
) -> MlReadinessStage:
    if models.shadow_count:
        return "shadow_ready"
    if models.registered_count:
        return "model_registered"
    if label_ready and evidence_ready:
        return "ready_for_training"
    if label_ready:
        return "awaiting_feature_evidence"
    return "collecting_labels"


def _current_lifecycle(
    registration_id: str,
    events: tuple[FeatureModelLifecycleEvent, ...] | list[FeatureModelLifecycleEvent],
) -> str:
    current = "challenger"
    previous_changed_at = None
    for event in sorted(events, key=lambda item: (item.changed_at, item.event_id)):
        if event.registration_id != registration_id or event.from_lifecycle != current:
            raise MLReadinessError("模型生命周期事件链不一致。")
        if previous_changed_at is not None and event.changed_at <= previous_changed_at:
            raise MLReadinessError("模型生命周期事件时间不递增。")
        current = event.to_lifecycle
        previous_changed_at = event.changed_at
    return current
