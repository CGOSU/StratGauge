import unittest
from datetime import UTC, datetime
from decimal import Decimal
from tempfile import TemporaryDirectory

from stratgauge.ml.drift import (
    DriftError,
    DriftPolicy,
    FeatureDriftStore,
    build_drift_report,
)


class DriftTests(unittest.TestCase):
    def test_stable_distribution_passes_and_is_idempotent(self) -> None:
        rows = tuple({"values": {"return_1": str(index)}} for index in range(10))
        policy = DriftPolicy(
            drift_policy_id="drift-test-v1",
            feature_names=("return_1",),
            minimum_samples=10,
            psi_threshold=Decimal("0.2"),
            mean_delta_threshold=Decimal("0"),
        )
        report = build_drift_report(
            baseline_rows=rows,
            current_rows=rows,
            baseline_source_sha256="baseline",
            current_source_sha256="current",
            policy=policy,
            generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertTrue(report.gate_passed)
        self.assertFalse(report.features[0].drifted)
        with TemporaryDirectory() as directory:
            store = FeatureDriftStore(directory)
            self.assertEqual(store.write(report), report)
            self.assertEqual(store.load(report.report_id), report)

    def test_shifted_distribution_fails_without_changing_a_model(self) -> None:
        baseline = tuple({"return_1": str(index)} for index in range(10))
        current = tuple({"return_1": str(index + 100)} for index in range(10))
        policy = DriftPolicy(
            drift_policy_id="drift-test-v1",
            feature_names=("return_1",),
            minimum_samples=10,
            psi_threshold=Decimal("0.2"),
            mean_delta_threshold=Decimal("1"),
        )
        report = build_drift_report(
            baseline_rows=baseline,
            current_rows=current,
            baseline_source_sha256="baseline",
            current_source_sha256="current",
            policy=policy,
            generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertFalse(report.gate_passed)
        self.assertTrue(report.features[0].drifted)

    def test_missing_feature_and_small_sample_are_blocked(self) -> None:
        policy = DriftPolicy(
            drift_policy_id="drift-test-v1",
            feature_names=("return_1",),
            minimum_samples=2,
        )
        with self.assertRaisesRegex(DriftError, "sample count"):
            build_drift_report(
                baseline_rows=({"return_1": "1"},),
                current_rows=({"return_1": "1"},),
                baseline_source_sha256="baseline",
                current_source_sha256="current",
                policy=policy,
                generated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        with self.assertRaisesRegex(DriftError, "missing"):
            build_drift_report(
                baseline_rows=({"other": "1"}, {"other": "2"}),
                current_rows=({"return_1": "1"}, {"return_1": "2"}),
                baseline_source_sha256="baseline",
                current_source_sha256="current",
                policy=policy,
                generated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )


if __name__ == "__main__":
    unittest.main()
