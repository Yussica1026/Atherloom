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
        for index in range(8):
            state, _ = homestead.act(state, {"action": "play"}, start + timedelta(days=4, minutes=31 * index))
        self.assertNotEqual(state["pet"]["mood"], "depressed")

    def test_school_builds_a_real_pet_skill(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        state = homestead.default_state(start.isoformat())
        state, _ = homestead.act(state, {"action": "adopt", "kind": "lop", "name": "米粒"}, start)
        state, events = homestead.act(state, {"action": "school", "subject": "painting"}, start)
        self.assertEqual(state["pet"]["skills"]["painting"], 1)
        self.assertTrue(any("绘画课" in event for event in events))


    def test_pet_can_rest_when_energy_is_low(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        state = homestead.default_state(start.isoformat())
        state, _ = homestead.act(state, {"action": "adopt", "kind": "cloud_cat", "name": "团团"}, start)
        state["pet"]["energy"] = 4
        state, events = homestead.act(state, {"action": "rest"}, start)
        self.assertEqual(state["pet"]["energy"], 49)
        self.assertTrue(any("休息" in event for event in events))
        self.assertIn("被子", state["pet"]["thought"])

    def test_play_and_school_have_real_cooldowns(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        state = homestead.default_state(start.isoformat())
        state, _ = homestead.act(state, {"action": "adopt", "kind": "lop", "name": "米粒"}, start)
        state, _ = homestead.act(state, {"action": "play"}, start)
        with self.assertRaisesRegex(ValueError, "喘口气"):
            homestead.act(state, {"action": "play"}, start + timedelta(minutes=10))
        state, _ = homestead.act(state, {"action": "school", "subject": "music"}, start)
        with self.assertRaisesRegex(ValueError, "下一堂课"):
            homestead.act(state, {"action": "school", "subject": "music"}, start + timedelta(hours=1))
        actions = homestead.allowed_actions(state, start + timedelta(hours=1))
        self.assertTrue(any(item["action"] == "play" for item in actions))
        self.assertFalse(any(item["action"] == "school" for item in actions))


if __name__ == "__main__":
    unittest.main()
