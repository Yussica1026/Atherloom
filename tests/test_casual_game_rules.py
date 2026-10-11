import random
import unittest

from backend.casual_games.games.base import RuleViolation
from backend.casual_games.games.blackjack import BlackjackEngine, hand_total
from backend.casual_games.games.bulls_and_cows import BullsAndCowsEngine
from backend.casual_games.games.rock_paper_scissors import RockPaperScissorsEngine
from backend.casual_games.games.tic_tac_toe import TicTacToeEngine
from backend.casual_games.games.twenty_questions import TwentyQuestionsEngine


class TicTacToeRulesTests(unittest.TestCase):
    def test_program_owns_turns_occupied_cells_and_win(self):
        engine = TicTacToeEngine()
        state = engine.initial_state({}, random.Random(1))
        original = dict(state)
        state = engine.apply_action(state, "user", {"position": 0}).state
        self.assertEqual(original["board"], [None] * 9)
        with self.assertRaises(RuleViolation) as wrong_turn:
            engine.apply_action(state, "user", {"position": 1})
        self.assertEqual(wrong_turn.exception.code, "not_actor_turn")
        with self.assertRaisesRegex(RuleViolation, "occupied"):
            engine.apply_action(state, "persona", {"position": 0})

        for actor, position in (
            ("persona", 3),
            ("user", 1),
            ("persona", 4),
            ("user", 2),
        ):
            transition = engine.apply_action(state, actor, {"position": position})
            state = transition.state
        self.assertEqual(state["status"], "finished")
        self.assertEqual(transition.result["winner"], "user")
        self.assertEqual(transition.result["outcome"], "user_win")
        self.assertEqual(transition.result["score"], {"user": 1, "persona": 0})

    def test_action_schema_rejects_unknown_fields_and_bool_positions(self):
        engine = TicTacToeEngine()
        state = engine.initial_state({}, random.Random(1))
        for action in ({"position": True}, {"position": 0, "comment": "not protocol"}):
            with self.assertRaises(RuleViolation):
                engine.apply_action(state, "user", action)


class RockPaperScissorsRulesTests(unittest.TestCase):
    def test_user_choice_is_hidden_until_persona_commits(self):
        engine = RockPaperScissorsEngine()
        state = engine.initial_state({}, random.Random(1))
        committed = engine.apply_action(state, "user", {"choice": "rock"})
        self.assertEqual(committed.state["user_choice"], "rock")
        public = engine.public_state(committed.state)
        self.assertNotIn("user_choice", public)
        self.assertTrue(public["user_choice_committed"])
        self.assertEqual(
            engine.public_action(committed.state, "user", {"choice": "rock"}),
            {"choice_committed": True},
        )

        resolved = engine.apply_action(committed.state, "persona", {"choice": "scissors"})
        self.assertEqual(resolved.result["winner"], "user")
        self.assertEqual(resolved.state["user_choice"], "rock")
        self.assertEqual(resolved.state["persona_choice"], "scissors")
        self.assertEqual(
            engine.public_action(resolved.state, "user", {"choice": "rock"}),
            {"choice": "rock"},
        )

    def test_persona_cannot_move_before_user(self):
        engine = RockPaperScissorsEngine()
        with self.assertRaises(RuleViolation) as caught:
            engine.apply_action(
                engine.initial_state({}, random.Random(1)),
                "persona",
                {"choice": "rock"},
            )
        self.assertEqual(caught.exception.code, "not_actor_turn")


class BullsAndCowsRulesTests(unittest.TestCase):
    def test_secrets_are_private_and_scoring_is_deterministic(self):
        engine = BullsAndCowsEngine()
        state = engine.initial_state({"user_secret": "0123"}, random.Random(7))
        self.assertEqual(len(state["persona_secret"]), 4)
        self.assertEqual(len(set(state["persona_secret"])), 4)
        public = engine.public_state(state)
        self.assertNotIn("user_secret", public)
        self.assertNotIn("persona_secret", public)

        user_turn = engine.apply_action(state, "user", {"guess": "4567"})
        scored = user_turn.events[0]
        expected_bulls = sum(
            left == right for left, right in zip(state["persona_secret"], "4567")
        )
        expected_cows = len(set(state["persona_secret"]) & set("4567")) - expected_bulls
        self.assertEqual((scored["bulls"], scored["cows"]), (expected_bulls, expected_cows))

        persona_turn = engine.apply_action(user_turn.state, "persona", {"guess": "0123"})
        self.assertEqual(persona_turn.result["winner"], "persona")
        self.assertEqual(persona_turn.state["status"], "finished")
        self.assertNotIn("user_secret", engine.public_state(persona_turn.state))

    def test_secret_and_guess_require_four_distinct_ascii_digits(self):
        engine = BullsAndCowsEngine()
        for secret in ("1123", "123", "１２３４", 1234):
            with self.assertRaises(RuleViolation):
                engine.initial_state({"user_secret": secret}, random.Random(1))
        state = engine.initial_state({"user_secret": "0123"}, random.Random(1))
        with self.assertRaises(RuleViolation):
            engine.apply_action(state, "user", {"guess": "0012"})


class BlackjackRulesTests(unittest.TestCase):
    @staticmethod
    def card(rank, suit="spades"):
        return {"rank": rank, "suit": suit}

    def test_deck_is_private_and_aces_score_as_one_or_eleven(self):
        engine = BlackjackEngine()
        state = engine.initial_state({}, random.Random(7))

        self.assertEqual(len(state["deck"]), 48)
        self.assertEqual(state["deck_remaining"], 48)
        self.assertEqual(len(state["user_hand"]), 2)
        self.assertEqual(len(state["persona_hand"]), 2)
        self.assertEqual(
            hand_total([self.card("A"), self.card("9")]),
            20,
        )
        self.assertEqual(
            hand_total([self.card("A"), self.card("A"), self.card("9")]),
            21,
        )
        self.assertEqual(
            hand_total(
                [self.card("A"), self.card("A"), self.card("9"), self.card("K")]
            ),
            21,
        )

        state["private_shuffle_marker"] = "must-not-leak"
        public = engine.public_state(state)
        self.assertNotIn("deck", public)
        self.assertNotIn("private_shuffle_marker", public)
        self.assertEqual(
            set(public),
            {
                "status",
                "turn",
                "user_hand",
                "persona_hand",
                "user_total",
                "persona_total",
                "user_stood",
                "persona_stood",
                "deck_remaining",
            },
        )

    def test_hit_turns_alternate_and_twenty_one_stands_automatically(self):
        engine = BlackjackEngine()
        state = {
            "status": "active",
            "turn": "user",
            "user_hand": [self.card("A"), self.card("9", "hearts")],
            "persona_hand": [self.card("10", "clubs"), self.card("7", "diamonds")],
            "user_total": 20,
            "persona_total": 17,
            "user_stood": False,
            "persona_stood": False,
            "deck": [self.card("4", "clubs"), self.card("A", "diamonds")],
            "deck_remaining": 2,
        }

        hit = engine.apply_action(state, "user", {"decision": "hit"})
        self.assertEqual(hit.state["user_total"], 21)
        self.assertTrue(hit.state["user_stood"])
        self.assertEqual(hit.state["turn"], "persona")
        self.assertEqual(hit.state["deck"], [self.card("4", "clubs")])
        self.assertEqual(hit.state["deck_remaining"], 1)
        self.assertEqual(
            [event["type"] for event in hit.events],
            ["card_drawn", "automatic_stand"],
        )
        self.assertEqual(hit.events[0]["deck_remaining"], 1)
        self.assertFalse(state["user_stood"])
        self.assertEqual(state["deck_remaining"], 2)

        finished = engine.apply_action(
            hit.state, "persona", {"decision": "stand"}
        )
        self.assertEqual(finished.state["status"], "finished")
        self.assertEqual(finished.result["winner"], "user")
        self.assertEqual(finished.result["score"], {"user": 21, "persona": 17})

    def test_bust_loses_immediately_and_twenty_one_cannot_hit(self):
        engine = BlackjackEngine()
        state = {
            "status": "active",
            "turn": "user",
            "user_hand": [self.card("K"), self.card("Q", "hearts")],
            "persona_hand": [self.card("10", "clubs"), self.card("8", "diamonds")],
            "user_total": 20,
            "persona_total": 18,
            "user_stood": False,
            "persona_stood": False,
            "deck": [self.card("5", "clubs")],
            "deck_remaining": 1,
        }
        busted = engine.apply_action(state, "user", {"decision": "hit"})
        self.assertEqual(busted.state["status"], "finished")
        self.assertEqual(busted.result["winner"], "persona")
        self.assertEqual(busted.result["score"], {"user": 25, "persona": 18})
        self.assertEqual(busted.events[-1]["type"], "game_finished")
        self.assertEqual(busted.events[-1]["reason"], "user_bust")

        twenty_one = {
            **state,
            "user_hand": [self.card("A"), self.card("K", "hearts")],
            "user_total": 21,
        }
        with self.assertRaises(RuleViolation) as caught:
            engine.apply_action(twenty_one, "user", {"decision": "hit"})
        self.assertEqual(caught.exception.code, "cannot_hit")

    def test_stood_player_is_skipped_until_the_other_player_stands(self):
        engine = BlackjackEngine()
        state = {
            "status": "active",
            "turn": "user",
            "user_hand": [self.card("10"), self.card("5", "hearts")],
            "persona_hand": [self.card("10", "clubs"), self.card("6", "diamonds")],
            "user_total": 15,
            "persona_total": 16,
            "user_stood": False,
            "persona_stood": False,
            "deck": [self.card("2", "clubs"), self.card("2", "diamonds")],
            "deck_remaining": 2,
        }

        user_hit = engine.apply_action(state, "user", {"decision": "hit"})
        self.assertEqual(user_hit.state["user_total"], 17)
        self.assertEqual(user_hit.state["turn"], "persona")
        persona_stand = engine.apply_action(
            user_hit.state, "persona", {"decision": "stand"}
        )
        self.assertEqual(persona_stand.events, [
            {"type": "hand_stood", "actor": "persona"}
        ])
        self.assertEqual(persona_stand.state["turn"], "user")
        user_hit_again = engine.apply_action(
            persona_stand.state, "user", {"decision": "hit"}
        )
        self.assertEqual(user_hit_again.state["user_total"], 19)
        self.assertEqual(user_hit_again.state["turn"], "user")
        finished = engine.apply_action(
            user_hit_again.state, "user", {"decision": "stand"}
        )
        self.assertEqual(finished.result["winner"], "user")
        self.assertEqual(finished.result["score"], {"user": 19, "persona": 16})
        self.assertEqual(finished.events[-1]["reason"], "both_stood")
        self.assertEqual(
            finished.result["notable_events"][:3],
            ["finish_reason:both_stood", "user_total:19", "persona_total:16"],
        )

    def test_decision_shape_is_strict(self):
        engine = BlackjackEngine()
        state = engine.initial_state({}, random.Random(11))
        for action in (
            {"decision": "double"},
            {"decision": "stand", "comment": "extra"},
            {},
        ):
            with self.subTest(action=action), self.assertRaises(RuleViolation):
                engine.apply_action(state, "user", action)


class TwentyQuestionsRulesTests(unittest.TestCase):
    def test_user_is_the_oracle_and_correct_guess_finishes(self):
        engine = TwentyQuestionsEngine()
        state = engine.initial_state({}, random.Random(1))
        question = engine.apply_action(
            state,
            "persona",
            {"kind": "question", "text": "Is it alive?"},
        )
        answered = engine.apply_action(question.state, "user", {"answer": "yes"})
        self.assertEqual(answered.state["turn"], "persona")
        guess = engine.apply_action(
            answered.state,
            "persona",
            {"kind": "guess", "text": "a cat"},
        )
        finished = engine.apply_action(guess.state, "user", {"verdict": "correct"})
        self.assertEqual(finished.result["winner"], "persona")
        self.assertEqual(finished.state["status"], "finished")

    def test_twentieth_answer_ends_the_game_under_code_rules(self):
        engine = TwentyQuestionsEngine()
        state = engine.initial_state({}, random.Random(1))
        for index in range(20):
            asked = engine.apply_action(
                state,
                "persona",
                {"kind": "question", "text": f"question {index + 1}"},
            )
            answered = engine.apply_action(asked.state, "user", {"answer": "unknown"})
            state = answered.state
        self.assertEqual(state["status"], "finished")
        self.assertEqual(answered.result["winner"], "user")
        self.assertEqual(state["question_count"], 20)

    def test_answer_shape_depends_on_pending_kind(self):
        engine = TwentyQuestionsEngine()
        state = engine.initial_state({}, random.Random(1))
        guessed = engine.apply_action(
            state,
            "persona",
            {"kind": "guess", "text": "a tree"},
        )
        with self.assertRaises(RuleViolation):
            engine.apply_action(guessed.state, "user", {"answer": "no"})


if __name__ == "__main__":
    unittest.main()
