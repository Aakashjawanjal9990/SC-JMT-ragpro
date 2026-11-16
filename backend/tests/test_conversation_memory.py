from __future__ import annotations

import unittest

from app.conversation_memory import SlidingWindowManager, estimate_tokens
from app.models import ConversationTurn


class SlidingWindowManagerTests(unittest.TestCase):
    def test_keeps_newest_complete_exchanges_within_budget(self) -> None:
        manager = SlidingWindowManager(
            max_messages=6,
            max_context_tokens=1_500,
            output_reserve_tokens=200,
        )
        turns = [
            turn
            for index in range(1, 5)
            for turn in (
                ConversationTurn(role="user", text=f"question {index}"),
                ConversationTurn(role="assistant", text=f"answer {index}"),
            )
        ]

        context = manager.build(
            "Earlier discussion summary.",
            turns,
            question="What did the latest judgment decide?",
            evidence=["A relevant passage from the judgment."],
        )

        self.assertEqual(
            [turn.text for turn in context.recent_turns],
            ["question 2", "answer 2", "question 3", "answer 3", "question 4", "answer 4"],
        )
        self.assertLessEqual(context.estimated_tokens, 1_500)

    def test_skips_older_exchanges_after_newer_exchange_does_not_fit(self) -> None:
        manager = SlidingWindowManager(
            max_messages=6,
            max_context_tokens=1_300,
            output_reserve_tokens=200,
        )
        turns = [
            ConversationTurn(role="user", text="old question"),
            ConversationTurn(role="assistant", text="old answer"),
            ConversationTurn(role="user", text="middle question"),
            ConversationTurn(role="assistant", text="m" * 2_000),
            ConversationTurn(role="user", text="new question"),
            ConversationTurn(role="assistant", text="new answer"),
        ]

        context = manager.build(
            "",
            turns,
            question="Current question",
            evidence=["Evidence"],
        )

        self.assertEqual(
            [turn.text for turn in context.recent_turns],
            ["new question", "new answer"],
        )

    def test_evidence_is_capped_and_counted_inside_context_budget(self) -> None:
        manager = SlidingWindowManager(
            max_messages=4,
            max_context_tokens=1_100,
            output_reserve_tokens=200,
        )

        context = manager.build(
            "summary " * 200,
            [
                ConversationTurn(role="user", text="earlier question"),
                ConversationTurn(role="assistant", text="earlier answer"),
            ],
            question="Current question",
            evidence=["top evidence " * 1000, "lower ranked evidence"],
        )

        self.assertEqual(len(context.evidence_texts), 1)
        self.assertLessEqual(context.estimated_tokens, 1_100)
        self.assertGreater(estimate_tokens(context.evidence_texts[0]), 0)


if __name__ == "__main__":
    unittest.main()
