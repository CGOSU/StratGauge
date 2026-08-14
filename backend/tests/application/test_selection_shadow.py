import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.application.selection_features import SelectionFeatureSnapshotReport
from stratgauge.application.selection_shadow import score_selection_feature_report
from stratgauge.domain.models import ModelLifecycle
from stratgauge.features.contract import FeatureSnapshot
from stratgauge.features.store import FeatureSnapshotStore
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.selection_training import BaselineModel
from stratgauge.ml.shadow import ShadowScoreStore, ShadowScoringInput, shadow_input_sha256


class SelectionShadowTests(unittest.TestCase):
    def test_feature_report_is_scored_as_shadow_only_with_content_hash(self) -> None:
        model = BaselineModel(
            model_version_id="baseline-shadow-v1",
            feature_set_version_id="features-local-v1",
            tracking_policy_id="policy-local-v1",
            positive_probability=Decimal("0.6"),
            train_row_count=10,
            train_positive_count=6,
            random_seed=0,
            source_file_sha256="source-hash",
        )
        with TemporaryDirectory() as directory:
            feature_store = FeatureSnapshotStore(directory)
            snapshot = _snapshot()
            feature_store.write(snapshot)
            report = SelectionFeatureSnapshotReport(
                report_id="feature-report-fixture",
                batch_id="batch-fixture",
                feature_set_version_id=snapshot.feature_set_version_id,
                as_of=snapshot.as_of,
                source_snapshot_ids=("snapshot-local",),
                candidate_snapshot_ids=(("candidate-local", snapshot.feature_snapshot_id),),
            )
            registry = ModelRegistryStore(directory)
            registry.register(
                model,
                model_artifact=model.model_dump_json(indent=2).encode("utf-8"),
                evaluation_report=b'{"log_loss": 0.5}',
                training_manifest=json.dumps(
                    {
                        "model_version_id": model.model_version_id,
                        "feature_set_version_id": model.feature_set_version_id,
                        "tracking_policy_id": model.tracking_policy_id,
                        "source_file_sha256": model.source_file_sha256,
                    },
                    sort_keys=True,
                ).encode("utf-8"),
                registered_at=datetime(2026, 1, 1, tzinfo=UTC),
                registered_by="test",
            )
            registry.transition(
                model.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="shadow started",
                operator="test",
            )

            scored = score_selection_feature_report(
                report,
                feature_store=feature_store,
                registry=registry,
                score_store=ShadowScoreStore(directory),
                model_version_id=model.model_version_id,
                scored_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )

            self.assertEqual(len(scored.score_ids), 1)
            self.assertEqual(scored.failures, ())
            self.assertEqual(scored.model_lifecycle, "shadow")
            self.assertEqual(
                scored.source_input_sha256, shadow_input_sha256((self._input(report, snapshot),))
            )
            score = ShadowScoreStore(directory).load_score(scored.score_ids[0])
            self.assertEqual(score.uncalibrated_model_score, Decimal("0.6"))
            self.assertFalse(score.used_for_decision)

    @staticmethod
    def _input(
        report: SelectionFeatureSnapshotReport,
        snapshot: FeatureSnapshot,
    ) -> ShadowScoringInput:
        return ShadowScoringInput(
            candidate_id=report.candidate_snapshot_ids[0][0],
            feature_snapshot=snapshot,
        )


def _snapshot() -> FeatureSnapshot:
    as_of = datetime(2026, 1, 5, 16, tzinfo=UTC)
    return FeatureSnapshot(
        feature_snapshot_id="feature-local-v1",
        feature_set_version_id="features-local-v1",
        security_id="security-local",
        symbol="000001.SZ",
        selection_date=date(2026, 1, 5),
        as_of=as_of,
        available_at=as_of,
        input_session_start=date(2025, 12, 1),
        input_session_end=date(2025, 12, 31),
        source_snapshot_ids=("snapshot-local",),
        values={"return_1": Decimal("0.01")},
    )


if __name__ == "__main__":
    unittest.main()
