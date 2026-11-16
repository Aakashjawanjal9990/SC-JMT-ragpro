from __future__ import annotations

import unittest

from app.adapters.support_validation import AnswerSupportValidator
from app.grounding import all_claims_supported, parse_grounded_answer
from app.ports import DependencyUnavailable


class GroundedAnswerParsingTests(unittest.TestCase):
    def test_extracts_sentence_specific_evidence_and_normalizes_markers(self) -> None:
        parsed = parse_grounded_answer(
            "The appeal was allowed. [E1] Costs were not awarded. [P2]",
            ["E1", "E2"],
            {"E1", "E2"},
        )

        self.assertIsNotNone(parsed)
        if parsed is None:
            self.fail("Expected sentence citations to parse")
        self.assertEqual(
            [claim.text for claim in parsed.claims],
            ["The appeal was allowed.", "Costs were not awarded."],
        )
        self.assertEqual([claim.evidence_ids for claim in parsed.claims], [("E1",), ("E2",)])
        self.assertEqual(
            parsed.rendered_answer,
            "The appeal was allowed [1]. Costs were not awarded [2].",
        )

    def test_rejects_any_uncited_sentence(self) -> None:
        parsed = parse_grounded_answer(
            "The appeal was allowed. [E1] Costs were not awarded.",
            ["E1"],
            {"E1"},
        )

        self.assertIsNone(parsed)

    def test_rejects_citations_not_returned_or_not_available(self) -> None:
        self.assertIsNone(
            parse_grounded_answer("The appeal was allowed. [E2]", ["E1"], {"E1", "E2"})
        )
        self.assertIsNone(parse_grounded_answer("The appeal was allowed. [E1]", ["E1"], set()))

    def test_support_verdict_parser_requires_one_boolean_per_sentence(self) -> None:
        self.assertEqual(
            AnswerSupportValidator._parse('{"supported": [true, false]}', 2),
            [True, False],
        )
        with self.assertRaises(DependencyUnavailable):
            AnswerSupportValidator._parse('{"supported": [true]}', 2)
        with self.assertRaises(DependencyUnavailable):
            AnswerSupportValidator._parse('{"supported": ["yes"]}', 1)

    def test_accepts_only_literal_true_for_every_claim(self) -> None:
        self.assertTrue(all_claims_supported([True, True], 2))
        self.assertFalse(all_claims_supported([True, False], 2))
        self.assertFalse(all_claims_supported([True], 2))
        self.assertFalse(all_claims_supported(["true"], 1))  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
