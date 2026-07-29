import unittest
from datetime import datetime, timedelta, timezone

from backend import homestead


class HomesteadCoreTests(unittest.TestCase):
    def test_unwatered_flower_can_wilt_and_die(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        state = homestead.default_state(start.isoformat())
        state, _ = homestead.act(state, {"action": "plant", "target": 0, "species": "sunbell"}, start)
        state, events = homestead.settle(state, start + timedelta(days=4))
        self.assertEqual(state["garden"][0]["status"], "dead")
        self.assertTrue(any("枯死" in event for event in events))

    def test_neglected_pet_becomes_depressed_and_can_recover(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        state = homestead.default_state(start.isoformat())
        state, _ = homestead.act(state, {"action": "adopt", "kind": "cloud_cat", "name": "团团"}, start)
        state, _ = homestead.settle(state, start + timedelta(days=4))
        self.assertEqual(state["pet"]["mood"], "depressed")
        state["inventory"]["pet_food"] = 3
        state, _ = homestead.act(state, {"action": "feed"}, start + timedelta(days=4))
        for _ in range(8):
            state, _ = homestead.act(state, {"action": "play"}, start + timedelta(days=4))
        self.assertNotEqual(state["pet"]["mood"], "depressed")

    def test_school_builds_a_real_pet_skill(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        state = homestead.default_state(start.isoformat())
        state, _ = homestead.act(state, {"action": "adopt", "kind": "lop", "name": "米粒"}, start)
        state, events = homestead.act(state, {"action": "school", "subject": "painting"}, start)
        self.assertEqual(state["pet"]["skills"]["painting"], 1)
        self.assertTrue(any("绘画课" in event for event in events))


if __name__ == "__main__":
    unittest.main()
