"""Subprocess tests for the SessionStart hook and manual mode."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CAPTURE = PROJECT / "capture.py"
REHYDRATE = PROJECT / "rehydrate.py"
TRANSCRIPT = PROJECT / "tests" / "fixtures" / "transcript.jsonl"


class RehydrateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "private-data"
        self.capture("card-session")

    def tearDown(self):
        self.temporary.cleanup()

    def environment(self):
        environment = os.environ.copy()
        environment["PRECOMPACT_DIR"] = str(self.root)
        return environment

    def capture(self, session_id):
        payload = {
            "session_id": session_id,
            "cwd": "/work/project",
            "transcript_path": str(TRANSCRIPT),
            "trigger": "auto",
        }
        return subprocess.run(
            [sys.executable, str(CAPTURE)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.environment(),
            check=False,
        )

    def hook(self, payload):
        return subprocess.run(
            [sys.executable, str(REHYDRATE)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.environment(),
            check=False,
        )

    def test_non_compact_source_prints_nothing_even_with_checkpoint(self):
        """source: rehydrate.py inject returns silently unless SessionStart source equals compact."""
        compact = self.hook({"session_id": "card-session", "source": "compact"})
        result = self.hook({"session_id": "card-session", "source": "startup"})
        self.assertIn("## Before compaction (raw floor)", compact.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_card_shows_last_user_intent_paths_and_turn_count(self):
        """source: rehydrate.py _floor_card builds the intent, recent paths, and checkpoint count lines."""
        result = self.hook({"session_id": "card-session", "source": "compact"})
        self.assertEqual(result.returncode, 0)
        self.assertIn(
            "Context protector: working memory from before this compaction. Orientation only; check specifics before relying on them.",
            result.stdout,
        )
        self.assertIn("- Last user intent: Keep the parser behavior stable.", result.stdout)
        self.assertIn("- Files in play: /work/project/demo.py, ~/notes/plan.md", result.stdout)
        self.assertRegex(result.stdout, r"- 3 turns checkpointed at \d{8}T\d{6}Z-\d{6}")

    def test_rehydrate_file_precedes_raw_floor(self):
        """source: rehydrate.py build_card appends REHYDRATE.md content before the checkpoint floor."""
        session = self.root / "sessions" / "card-session"
        (session / "REHYDRATE.md").write_text("## User summary\nSynthetic summary.", encoding="utf-8")
        result = self.hook({"session_id": "card-session", "source": "compact"})
        self.assertEqual(result.returncode, 0)
        self.assertLess(result.stdout.index("## User summary"), result.stdout.index("## Before compaction (raw floor)"))

    def test_card_is_capped_and_ends_with_truncation_marker(self):
        """source: rehydrate.py build_card enforces MAX_CARD_CHARS and appends the truncation marker."""
        session = self.root / "sessions" / "card-session"
        (session / "REHYDRATE.md").write_text("R" * 4000, encoding="utf-8")
        result = self.hook({"session_id": "card-session", "source": "compact"})
        self.assertEqual(result.returncode, 0)
        card = result.stdout.rstrip("\n")
        self.assertEqual(len(card), 2600)
        self.assertTrue(card.endswith("...(truncated)"))

    def test_compact_without_checkpoint_or_user_card_is_silent(self):
        """source: rehydrate.py inject prints nothing when build_card has neither REHYDRATE.md nor a checkpoint."""
        existing = self.hook({"session_id": "card-session", "source": "compact"})
        result = self.hook({"session_id": "empty-session", "source": "compact"})
        self.assertIn("## Before compaction (raw floor)", existing.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_manual_mode_prints_same_card_and_missing_session_exits_one(self):
        """source: rehydrate.py manual implements --session output and its documented missing-checkpoint error."""
        hook_result = self.hook({"session_id": "card-session", "source": "compact"})
        manual_result = subprocess.run(
            [sys.executable, str(REHYDRATE), "--session", "card-session"],
            input="",
            text=True,
            capture_output=True,
            env=self.environment(),
            check=False,
        )
        self.assertEqual(manual_result.returncode, 0)
        self.assertEqual(manual_result.stdout, hook_result.stdout)
        missing = subprocess.run(
            [sys.executable, str(REHYDRATE), "--session", "not-created"],
            input="",
            text=True,
            capture_output=True,
            env=self.environment(),
            check=False,
        )
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stderr, "no checkpoint for not-created\n")

    def test_rehydrate_uses_newest_checkpoint_filename(self):
        """source: rehydrate.py _latest_checkpoint selects the lexically newest timestamped checkpoint."""
        next_transcript = Path(self.temporary.name) / "newer.jsonl"
        next_transcript.write_text(
            json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "The newer request wins."},
            }) + "\n",
            encoding="utf-8",
        )
        payload = {
            "session_id": "card-session",
            "cwd": "/work/project",
            "transcript_path": str(next_transcript),
            "trigger": "manual",
        }
        captured = subprocess.run(
            [sys.executable, str(CAPTURE)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.environment(),
            check=False,
        )
        result = self.hook({"session_id": "card-session", "source": "compact"})
        self.assertEqual(captured.returncode, 0)
        self.assertIn("- Last user intent: The newer request wins.", result.stdout)
        self.assertNotIn("- Last user intent: Keep the parser behavior stable.", result.stdout)


if __name__ == "__main__":
    unittest.main()
