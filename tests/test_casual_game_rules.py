import random
import unittest

from backend.casual_games.games.base import RuleViolation
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
