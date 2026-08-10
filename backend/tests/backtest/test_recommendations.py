import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from stratgauge.backtest.recommendations import build_recommendation_workbench
from stratgauge.backtest.selection_stats import SelectionOutcomeStats
from stratgauge.domain.models import Exchange
from stratgauge.domain.selections import SelectionBatch, SelectionCandidate
from stratgauge.ml.shadow import ShadowScore


class RecommendationTests(unittest.TestCase):
    def test_workbench_keeps_complete_evidence_reviewable_and_non_authoritative(self) -> None:
        batch, candidate = _fixture()
        result = build_recommendation_workbench(
            batch=batch,
            candidates=(candidate,),
            quality_states={candidate.candidate_id: "approved"},
            historical_stats={candidate.candidate_id: _stats()},
            model_scores={candidate.candidate_id: _score(candidate.candidate_id)},
            labels={candidate.candidate_id: None},
            generated_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
        )

        item = result.candidates[0]
        self.assertEqual(item.state, "reviewable")
        self.assertFalse(item.authoritative)
        self.assertFalse(result.automatic_action)
        self.assertEqual(item.blocking_reasons, ())

    def test_missing_evidence_blocks_without_inventing_defaults(self) -> None:
        batch, candidate = _fixture()
        result = build_recommendation_workbench(
            batch=batch,
            candidates=(candidate,),
            quality_states={},
            historical_stats={},
            model_scores={},
            labels={},
            generated_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
        )

        item = result.candidates[0]
        self.assertEqual(item.state, "blocked")
        self.assertIsNone(item.historical_stats)
        self.assertIsNone(item.model_score)
        self.assertEqual(
            item.blocking_reasons,
            (
                "quality:unknown",
                "historical_evidence_missing",
                "model_score_missing",
            ),
        )

    def test_decision_score_cannot_enter_workbench(self) -> None:
        batch, candidate = _fixture()
        score = _score(candidate.candidate_id).model_copy(update={"used_for_decision": True})
        with self.assertRaisesRegex(ValueError, "used for a decision"):
            build_recommendation_workbench(
                batch=batch,
                candidates=(candidate,),
                quality_states={candidate.candidate_id: "approved"},
                historical_stats={candidate.candidate_id: _stats()},
                model_scores={candidate.candidate_id: score},
                labels={},
                generated_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
            )


def _fixture() -> tuple[SelectionBatch, SelectionCandidate]:
    batch = SelectionBatch(
        batch_id="batch-recommendation",
        source="manual",
        selection_date=date(2026, 1, 5),
        exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
        imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
        formula_name="fixture",
        source_file_sha256="a" * 64,
        candidate_count=1,
    )
    candidate = SelectionCandidate(
        candidate_id="candidate-recommendation",
        batch_id=batch.batch_id,
        local_code="000001",
        symbol="000001.SZ",
        exchange=Exchange.SZ,
        name="fixture",
    )
    return batch, candidate


def _stats() -> SelectionOutcomeStats:
    return SelectionOutcomeStats(
        total=100,
        completed=100,
        wins=60,
        losses=40,
        flats=0,
        no_entry=0,
        unresolved=0,
        minimum_evidence_samples=100,
        evidence_sufficient=True,
        win_rate=Decimal("0.6"),
        average_net_return=Decimal("0.01"),
    )


def _score(candidate_id: str) -> ShadowScore:
    return ShadowScore(
        score_id="score-recommendation",
        model_version_id="model-recommendation",
        model_lifecycle="active",
        candidate_id=candidate_id,
        symbol="000001.SZ",
        selection_date=date(2026, 1, 5),
        feature_set_version_id="features-v1",
        feature_snapshot_id="feature-snapshot-v1",
        tracking_policy_id="policy-v1",
        source_snapshot_ids=("snapshot-v1",),
        scored_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
        uncalibrated_model_score=Decimal("0.6"),
    )


if __name__ == "__main__":
    unittest.main()
