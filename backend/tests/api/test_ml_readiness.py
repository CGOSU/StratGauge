import unittest
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from stratgauge.api.app import create_app
from stratgauge.data_pipeline.selection_store import SelectionImportStore


class MLReadinessApiTests(unittest.TestCase):
    def test_readiness_endpoint_returns_actionable_blockers(self) -> None:
        with TemporaryDirectory() as directory:
            store = SelectionImportStore(directory)
            client = TestClient(
                create_app(
                    selection_store=store,
                    data_root=directory,
                    model_root=directory,
                )
            )

            response = client.get("/v1/ml/readiness")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["purpose"], "candidate_success")
            self.assertEqual(payload["stage"], "collecting_labels")
            self.assertEqual(payload["accepted_label_count"], 0)
            self.assertEqual(payload["minimum_labeled_samples"], 100)
            self.assertFalse(payload["training_ready"])
            self.assertTrue(any("100" in blocker for blocker in payload["blockers"]))


if __name__ == "__main__":
    unittest.main()
