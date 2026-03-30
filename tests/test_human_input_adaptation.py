from __future__ import annotations

import unittest
import uuid

from core.human_input_adapter import adapt_user_input, learn_user_shorthand
from core.input_normalizer import normalize_user_text
from core.task_router import classify
from storage.migrations import run_migrations


class HumanInputAdaptationTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_normalizer_handles_shorthand_and_typos(self) -> None:
        result = normalize_user_text("pls hlp me harden tg bot so no passwrods leak")
        self.assertIn("please", result.normalized_text)
        self.assertIn("help", result.normalized_text)
        self.assertIn("telegram", result.normalized_text)
        self.assertIn("passwords", result.normalized_text)
        self.assertIn("shorthand_heavy", result.quality_flags)
        self.assertIn("typo_heavy", result.quality_flags)

    def test_reference_resolution_uses_session_subject(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        first = adapt_user_input(
            "Thomas keeps the knowledge shard for telegram bot routing.",
            session_id=session_id,
        )
        self.assertIn("knowledge shard", first.topic_hints)

        second = adapt_user_input(
            "if that one dies other one can still have it right?",
            session_id=session_id,
        )
        self.assertTrue(second.reference_targets)
        self.assertIn("knowledge shard", " ".join(second.reference_targets))
        self.assertIn("Context subject:", second.reconstructed_text)
        self.assertGreater(second.understanding_confidence, 0.45)

    def test_session_lexicon_improves_classification(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        learn_user_shorthand("mng", "meet and greet", session_id=session_id)
        interpreted = adapt_user_input(
            "pls make mng server for swarm entry",
            session_id=session_id,
        )
        self.assertIn("meet and greet", interpreted.normalized_text)
        classification = classify(interpreted.reconstructed_text, context=interpreted.as_context())
        self.assertEqual(classification["task_class"], "system_design")

    def test_normalizer_preserves_structured_workspace_file_prompt(self) -> None:
        prompt = "Inside /tmp/nulla_local_tooling_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH"
        result = normalize_user_text(prompt)

        self.assertEqual(result.normalized_text, prompt)
        self.assertEqual(result.replacements, {})

    def test_normalizer_preserves_multiline_code_prompt(self) -> None:
        prompt = (
            "Inside /tmp/nulla_local_tooling_truth/alpha create adder.py with exactly this code:\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        result = normalize_user_text(prompt)

        self.assertEqual(result.normalized_text, prompt.strip())
        self.assertIn("adder.py", result.normalized_text)
        self.assertIn("->", result.normalized_text)

    def test_adapt_user_input_preserves_multiline_code_prompt(self) -> None:
        prompt = (
            "Inside /tmp/nulla_local_tooling_truth/alpha create adder.py with exactly this code:\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        interpreted = adapt_user_input(prompt, session_id=f"session-{uuid.uuid4().hex}")

        self.assertEqual(interpreted.normalized_text, prompt.strip())
        self.assertEqual(interpreted.reconstructed_text, prompt.strip())

    def test_structured_workspace_prompts_do_not_infer_semantic_topics_from_paths(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        first = adapt_user_input(
            "Create a folder named alpha inside /tmp/openclaw_workspace_truth.",
            session_id=session_id,
        )
        second = adapt_user_input(
            "Inside /tmp/openclaw_workspace_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH",
            session_id=session_id,
        )

        self.assertEqual(first.topic_hints, [])
        self.assertEqual(first.reference_targets, [])
        self.assertNotIn("Context subject:", first.reconstructed_text)
        self.assertEqual(second.topic_hints, [])
        self.assertEqual(second.reference_targets, [])
        self.assertEqual(
            second.reconstructed_text,
            "Inside /tmp/openclaw_workspace_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH",
        )


if __name__ == "__main__":
    unittest.main()
