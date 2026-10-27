"""Subprocess tests for the PreCompact hook."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
CAPTURE = PROJECT / "capture.py"
TRANSCRIPT = PROJECT / "tests" / "fixtures" / "transcript.jsonl"


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "private-data"

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, payload, stdin=None, root=None):
        environment = os.environ.copy()
        environment["PRECOMPACT_DIR"] = str(root or self.root)
        return subprocess.run(
            [sys.executable, str(CAPTURE)],
            input=json.dumps(payload) if stdin is None else stdin,
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

    def event(self, session_id="sample-session", transcript=str(TRANSCRIPT)):
        return {
            "session_id": session_id,
            "cwd": "/work/project",
            "transcript_path": transcript,
            "trigger": "manual",
        }

    def checkpoints(self, session_id="sample-session", root=None):
        selected_root = Path(root or self.root)
        directory = selected_root / "sessions" / session_id
        return sorted(directory.glob("checkpoint-*.json")) if directory.exists() else []

    def test_invalid_stdin_is_silent_and_does_not_create_files(self):
        """source: capture.py main rejects malformed stdin with exit 0 and no files, while a following valid hook call still checkpoints."""
        invalid = self.invoke({}, stdin="{invalid json")
        self.assertEqual(invalid.returncode, 0)
        self.assertEqual(invalid.stdout, "")
        self.assertEqual(list(self.root.rglob("*")) if self.root.exists() else [], [])
        valid = self.invoke(self.event())
        self.assertEqual(valid.returncode, 0)
        self.assertEqual(len(self.checkpoints()), 1)

    def test_traversal_session_id_uses_unknown_directory(self):
        """source: common.py safe_session_id and session_dir contain traversal ids beneath sessions/unknown."""
        result = self.invoke(self.event(session_id="../../etc", transcript=""))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.checkpoints("unknown")), 1)
        self.assertFalse((self.root / "etc").exists())

    def test_checkpoint_and_directories_have_private_modes(self):
        """source: common.py _private_directory and write_checkpoint set directory mode 0700 and checkpoint mode 0600."""
        result = self.invoke(self.event())
        self.assertEqual(result.returncode, 0)
        checkpoint = self.checkpoints()[0]
        for directory in (self.root, self.root / "sessions", checkpoint.parent):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o600)
        log_file = self.root / "protector.log"
        self.assertEqual(log_file.stat().st_mode & 0o777, 0o600)

    def test_two_captures_in_the_same_second_keep_both_files(self):
        """source: common.py checkpoint_stamp adds microseconds and write_checkpoint uses distinct checkpoint destinations."""
        for _ in range(6):
            result = self.invoke(self.event())
            self.assertEqual(result.returncode, 0)
            files = self.checkpoints()
            stamps = [
                path.name.removeprefix("checkpoint-").removesuffix(".json")
                for path in files
            ]
            seconds = [stamp.rsplit("-", 1)[0] for stamp in stamps]
            if any(seconds.count(second) >= 2 for second in set(seconds)):
                self.assertGreaterEqual(len(files), 2)
                self.assertTrue(
                    all(re.fullmatch(r"\d{8}T\d{6}Z-\d{6}", stamp) for stamp in stamps)
                )
                self.assertEqual(len(stamps), len(set(stamps)))
                return
        self.fail("six rapid captures did not include two in the same second")

    def test_precompact_dir_is_honored(self):
        """source: common.py data_root honors a nonempty PRECOMPACT_DIR value."""
        alternate = Path(self.temporary.name) / "chosen-root"
        result = self.invoke(self.event(), root=alternate)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.checkpoints(root=alternate)), 1)
        self.assertFalse(self.root.exists())

    def test_checkpoint_contains_capture_fields_and_parsed_turns(self):
        """source: capture.py main writes the context_event checkpoint schema from the PreCompact JSON object."""
        result = self.invoke(self.event())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        checkpoint = json.loads(self.checkpoints()[0].read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["kind"], "context_event")
        self.assertEqual(checkpoint["session_id"], "sample-session")
        self.assertEqual(checkpoint["cwd"], "/work/project")
        self.assertEqual(checkpoint["trigger"], "manual")
        self.assertEqual(checkpoint["turn_count"], 3)
        self.assertEqual(checkpoint["turns"][-1]["text"], "Keep the parser behavior stable.")
        self.assertRegex(checkpoint["ts"], r"^\d{8}T\d{6}Z-\d{6}$")

    def test_checkpoint_write_leaves_no_temporary_file(self):
        """source: common.py write_checkpoint moves its same-directory temporary file into place and removes it."""
        result = self.invoke(self.event())
        self.assertEqual(result.returncode, 0)
        directory = self.root / "sessions" / "sample-session"
        self.assertEqual(len(list(directory.glob("checkpoint-*.json"))), 1)
        self.assertEqual(list(directory.glob(".checkpoint-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
