import json
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.api.model_scores import ModelScoreService
from stratgauge.data_pipeline.selection_store import SelectionImportStore
from stratgauge.data_pipeline.tdx_import import import_tdx_export
from stratgauge.domain.models import ModelLifecycle
from stratgauge.ml.registry import ModelRegistryStore
from stratgauge.ml.selection_training import BaselineModel
from stratgauge.ml.shadow import ShadowScore, ShadowScoreStore

CONTENT = b"code,name,market\n000001,fixture,SZ\n"


class ModelScoreApiTests(unittest.TestCase):
    def test_candidate_model_scores_expose_read_only_evidence(self) -> None:
        imported = import_tdx_export(
            CONTENT,
            selection_date=date(2026, 1, 5),
            exported_at=datetime(2026, 1, 5, 15, tzinfo=UTC),
            imported_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            formula_name="fixture",
        )
        model = BaselineModel(
            model_version_id="baseline-api-v1",
            feature_set_version_id="features-fixture-v1",
            tracking_policy_id="policy-v1",
            positive_probability=Decimal("0.5"),
            train_row_count=20,
            train_positive_count=10,
            random_seed=0,
            source_file_sha256="source-hash",
        )
        with TemporaryDirectory() as directory:
            selection_store = SelectionImportStore(directory)
            selection_store.write(CONTENT, imported)
            registry = ModelRegistryStore(directory)
            registry.register(
                model,
                model_artifact=model.model_dump_json().encode("utf-8"),
                evaluation_report=b"{}",
                training_manifest=json.dumps(
                    {
                        "model_version_id": model.model_version_id,
                        "feature_set_version_id": model.feature_set_version_id,
                        "tracking_policy_id": model.tracking_policy_id,
                        "source_file_sha256": model.source_file_sha256,
                        "train_end": "2025-12-31",
                        "validation_end": "2026-01-31",
                        "row_counts": {"validation": 7},
                    }
                ).encode("utf-8"),
                registered_at=datetime(2026, 1, 1, tzinfo=UTC),
                registered_by="test",
            )
            registry.transition(
                model.model_version_id,
                ModelLifecycle.SHADOW,
                changed_at=datetime(2026, 1, 2, tzinfo=UTC),
                reason="shadow",
                operator="test",
            )
            candidate_id = imported.candidates[0].candidate_id
            score_store = ShadowScoreStore(directory)
            first_score = ShadowScore(
                score_id="shadow-score-api-v1",
                model_version_id=model.model_version_id,
                model_lifecycle="shadow",
                candidate_id=candidate_id,
                symbol="000001.SZ",
                selection_date=date(2026, 1, 5),
                feature_set_version_id="features-fixture-v1",
                feature_snapshot_id="feature-api-v1",
                tracking_policy_id="policy-v1",
                source_snapshot_ids=("snapshot-1",),
                scored_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
                uncalibrated_model_score=Decimal("0.5"),
            )
            score_store.write_score(first_score)
            score_store.write_score(
                first_score.model_copy(
                    update={
                        "score_id": "shadow-score-api-v2",
                        "scored_at": datetime(2026, 1, 5, 18, tzinfo=UTC),
                        "uncalibrated_model_score": Decimal("0.6"),
                    }
                )
            )
            service = ModelScoreService(
                selection_store,
                model_registry=registry,
                score_store=score_store,
            )
            with (
                patch.object(registry, "load", wraps=registry.load) as load_model,
                patch.object(
                    registry,
                    "read_training_manifest",
                    wraps=registry.read_training_manifest,
                ) as read_manifest,
            ):
                direct = service.get_candidate_scores(imported.batch.batch_id, candidate_id)
            self.assertEqual(direct.failures, ())
            self.assertEqual(len(direct.scores), 2)
            self.assertEqual(load_model.call_count, 2)
            self.assertEqual(read_manifest.call_count, 1)
            client = TestClient(create_app(selection_store=selection_store, model_root=directory))

            response = client.get(
                f"/v1/selection-batches/{imported.batch.batch_id}/candidates/{candidate_id}/model-scores"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["failures"], [])
        score = payload["scores"][0]
        self.assertEqual(score["model_version_id"], model.model_version_id)
        self.assertEqual(score["sample_count"], 7)
        self.assertFalse(score["calibrated"])
        self.assertFalse(score["authoritative"])
        self.assertEqual(score["applicable_date_end"], "2026-01-31")


if __name__ == "__main__":
    unittest.main()
