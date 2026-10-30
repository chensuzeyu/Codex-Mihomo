import unittest
from datetime import datetime, timezone

from codex_mihomo import selector as S


def node(samples, start=1000):
    history = [{"time": datetime.fromtimestamp(start + i * 10, timezone.utc).isoformat(), "delay": delay}
               for i, delay in enumerate(samples)]
    return {"extra": {S.HEALTH_URL: {"history": history}}}


class SelectorTests(unittest.TestCase):
    def test_mihomo_nanosecond_timestamps(self):
        self.assertEqual(S.timestamp("2026-10-08T02:40:07.589014587+00:00"),
                         S.timestamp("2026-10-08T02:40:07.589014Z"))
        for length in range(1, 10):
            fraction = "589130000"[:length]
            padded = fraction[:6].ljust(6, "0")
            self.assertEqual(S.timestamp("2026-10-08T02:40:07." + fraction + "Z"),
                             S.timestamp("2026-10-08T02:40:07." + padded + "+00:00"))

    def test_fault_bypasses_hold(self):
        state = {"switched_at": 1010}
        chosen, reason = S.choose(state, {"fast": node([200, 0]), "backup": node([400, 410])}, "fast", 1011)
        self.assertEqual(chosen, "backup")
        self.assertEqual(reason, "current-unhealthy-or-cooling")

    def test_cooldown_and_three_successes(self):
        record = S.update({}, S.history(node([0, 200, 200, 200])))
        self.assertFalse(S.eligible(record, 1031))
        record = S.update(record, S.history(node([200, 200], start=1170)))
        self.assertTrue(S.eligible(record, 1181))
        record = S.update({}, S.history(node([0, 200], start=1180)))
        record["until"] = 1000
        self.assertFalse(S.eligible(record, 1191))

    def test_duplicate_samples_not_counted_as_recovery(self):
        samples = S.history(node([0, 200]))
        record = S.update({}, samples)
        S.update(record, samples)
        self.assertEqual(len(record["samples"]), 2)
        self.assertEqual(record["streak"], 1)

    def test_quality_beats_best_single_latency(self):
        nodes = {"erratic": node([200, 3000, 200, 2900]), "steady": node([450] * 4)}
        self.assertEqual(S.choose({}, nodes, "missing", 1031)[0], "steady")

    def test_normal_improvement_respects_hold_and_margin(self):
        nodes = {"old": node([450] * 4), "new": node([200] * 4)}
        self.assertEqual(S.choose({"switched_at": 1020}, nodes, "old", 1031)[0], "old")
        self.assertEqual(S.choose({}, nodes, "old", 1031)[0], "new")
        self.assertEqual(S.choose({}, {"old": node([300] * 4), "new": node([200] * 4)}, "old", 1031)[0], "old")

    def test_stale_and_generic_checks_are_not_healthy(self):
        self.assertIsNone(S.choose({}, {"old": node([200])}, "old", 1100)[0])
        self.assertIsNone(S.choose({}, {"generic": {"history": [{"time": "2030-01-01T00:00:00Z", "delay": 1}]}}, "generic", 1031)[0])

    def test_no_healthy_candidate_preserves_connection_selection(self):
        self.assertEqual(S.choose({}, {"bad": node([0])}, "bad", 1001), (None, "no-healthy-candidate"))

    def test_removed_provider_nodes_are_discarded(self):
        state = {"nodes": {"other-provider": {"last": 1000}}}
        S.choose(state, {"new": node([300])}, "new", 1001)
        self.assertNotIn("other-provider", state["nodes"])

    def test_failed_checks_raise_score_after_recovery(self):
        recovered = S.update({}, S.history(node([0, 200, 200, 200])))
        steady = S.update({}, S.history(node([450] * 4)))
        self.assertGreater(S.score(recovered), S.score(steady))

    def test_entire_pool_failure_can_use_three_proven_recoveries(self):
        chosen, reason = S.choose({}, {"dead": node([0] * 4), "recovered": node([0, 300, 300, 300])}, "dead", 1031)
        self.assertEqual((chosen, reason), ("recovered", "emergency-proven-recovery"))

    def test_manual_pin_is_respected_only_when_healthy(self):
        class Controller:
            def __init__(self, failed=False):
                self.current = "steady"
                self.nodes = [{"name": "steady", **node([300] * 4)},
                              {"name": "pin", **node([600, 600, 600, 0 if failed else 600])}]

            def request(self, path, body=None):
                if body is not None:
                    self.current = body["name"]
                if path == "/providers/proxies":
                    return {"providers": {"subscription": {"proxies": self.nodes}}}
                group = {"type": "Selector", "all": ["steady", "pin"], "now": self.current}
                return {"proxies": {"OUTBOUND": group}} if path == "/proxies" else group

        controller = Controller()
        S.tick(controller, {}, 1031, "pin")
        self.assertEqual(controller.current, "pin")
        controller = Controller(failed=True)
        controller.current = "pin"
        S.tick(controller, {}, 1031, "pin")
        self.assertEqual(controller.current, "steady")


if __name__ == "__main__":
    unittest.main()
