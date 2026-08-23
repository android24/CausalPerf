import unittest

from causalperf_reference.decision import decide
from causalperf_reference.statistics import verify_a1_b_a2


class StatisticalVerifierTest(unittest.TestCase):
    def test_passes_reproducible_practical_improvement(self):
        result = verify_a1_b_a2(
            [1000 + offset for offset in range(-9, 11)],
            [800 + offset for offset in range(-9, 11)],
            [1005 + offset for offset in range(-9, 11)],
            absolute_threshold_ms=50,
            relative_threshold_percent=10,
            max_baseline_drift_percent=5,
            bootstrap_resamples=2000,
            seed=7,
        )
        self.assertEqual(result.status, "PASS")
        self.assertGreater(result.confidence_interval_ms[0], 0)

    def test_baseline_drift_is_inconclusive(self):
        result = verify_a1_b_a2(
            [1000.0] * 20,
            [800.0] * 20,
            [1300.0] * 20,
            absolute_threshold_ms=50,
            relative_threshold_percent=10,
            max_baseline_drift_percent=5,
            bootstrap_resamples=1000,
        )
        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertIn("BASELINE_DRIFT", result.reason_codes)

    def test_small_effect_fails(self):
        result = verify_a1_b_a2(
            [1000.0] * 20,
            [970.0] * 20,
            [1000.0] * 20,
            absolute_threshold_ms=50,
            relative_threshold_percent=10,
            max_baseline_drift_percent=5,
            bootstrap_resamples=1000,
        )
        self.assertEqual(result.status, "FAIL")

    def test_either_threshold_accepts_one_practical_threshold(self):
        result = verify_a1_b_a2(
            [1000.0] * 20, [940.0] * 20, [1000.0] * 20,
            absolute_threshold_ms=50, relative_threshold_percent=10,
            threshold_combination="either", max_baseline_drift_percent=5,
            bootstrap_resamples=1000,
        )
        self.assertEqual(result.status, "PASS")

    def test_expected_increase_uses_registered_direction(self):
        result = verify_a1_b_a2(
            [100.0] * 20, [130.0] * 20, [100.0] * 20,
            absolute_threshold_ms=20, relative_threshold_percent=20,
            expected_direction="increase", max_baseline_drift_percent=5,
            bootstrap_resamples=1000,
        )
        self.assertEqual(result.status, "PASS")


class CausalDecisionTest(unittest.TestCase):
    def test_missing_integrity_evidence_is_inconclusive(self):
        decision = decide(
            prediction_registered_at="2026-01-01T00:00:00Z",
            first_treatment_at="2026-01-01T00:00:01Z",
            integrity="INCONCLUSIVE", correctness="PASS", environment="PASS",
            mechanism="PASS", statistics="PASS", replication="PASS",
        )
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_missing_correctness_evidence_is_inconclusive(self):
        decision = decide(
            prediction_registered_at="2026-01-01T00:00:00Z",
            first_treatment_at="2026-01-01T00:00:01Z",
            integrity="PASS", correctness="INCONCLUSIVE", environment="PASS",
            mechanism="PASS", statistics="PASS", replication="PASS",
        )
        self.assertEqual(decision.verdict, "INCONCLUSIVE")

    def test_requires_preregistration(self):
        decision = decide(
            prediction_registered_at="2026-01-01T00:00:02Z",
            first_treatment_at="2026-01-01T00:00:01Z",
            integrity="PASS", correctness="PASS", environment="PASS",
            mechanism="PASS", statistics="PASS", replication="PASS",
        )
        self.assertEqual(decision.verdict, "INVALID")

    def test_all_gates_produce_local_causal_support(self):
        decision = decide(
            prediction_registered_at="2026-01-01T00:00:00Z",
            first_treatment_at="2026-01-01T00:00:01Z",
            integrity="PASS", correctness="PASS", environment="PASS",
            mechanism="PASS", statistics="PASS", replication="PASS",
        )
        self.assertEqual(decision.verdict, "CAUSALLY_SUPPORTED")
        self.assertEqual(decision.support_level, "C1")


if __name__ == "__main__":
    unittest.main()
