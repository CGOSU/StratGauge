from __future__ import annotations

import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory

from stratgauge.application.job_runner import (
    JobRecord,
    JobStatus,
    LocalJobError,
    LocalJobStore,
    job_id,
)

NOW = datetime(2026, 1, 5, 8, tzinfo=UTC)


class LocalJobStoreTests(unittest.TestCase):
    def test_job_identity_and_state_transitions_are_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalJobStore(directory)
            payload = {"task_id": "task-1", "attempt": 1}
            record = JobRecord(
                job_id=job_id(kind="selection_maturity", payload=payload),
                kind="selection_maturity",
                payload=payload,
                created_at=NOW,
                updated_at=NOW,
            )

            self.assertEqual(store.create(record), store.create(record))
            running = store.start(record.job_id, now=NOW)
            self.assertEqual(running.status, JobStatus.RUNNING)
            completed = store.finish(
                record.job_id,
                status=JobStatus.COMPLETED,
                now=NOW,
                result={"outcomes": 2},
            )
            self.assertEqual(completed.status, JobStatus.COMPLETED)
            self.assertEqual(store.start(record.job_id, now=NOW), completed)

    def test_running_job_cannot_be_claimed_twice(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalJobStore(directory)
            record = JobRecord(
                job_id="job-running-test",
                kind="test",
                created_at=NOW,
                updated_at=NOW,
            )
            store.create(record)
            store.start(record.job_id, now=NOW)

            with self.assertRaises(LocalJobError):
                store.start(record.job_id, now=NOW)


if __name__ == "__main__":
    unittest.main()
