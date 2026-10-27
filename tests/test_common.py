"""Tests for the shared transcript and path helpers."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import common


FIXTURE = Path(__file__).parent / "fixtures" / "transcript.jsonl"


class CommonTests(unittest.TestCase):
    def test_transcript_filters_events_and_flattens_content_blocks(self):
        """source: common.py parse_transcript_tail and _flatten_content keep only valid user and assistant message content."""
        turns = common.parse_transcript_tail(str(FIXTURE))
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant", "user"])
        self.assertIn("I will inspect the example.", turns[1]["text"])
        self.assertIn("[thinking] Check the parser behavior.", turns[1]["text"])
        self.assertIn('[tool_use:Read] {"file_path": "/work/project/demo.py"}', turns[1]["text"])
        self.assertIn("[tool_result] Synthetic file contents loaded.", turns[1]["text"])
        self.assertNotIn("Synthetic event outside", " ".join(turn["text"] for turn in turns))

    def test_missing_and_malformed_transcripts_return_empty(self):
        """source: common.py _read_tail_lines and parse_transcript_tail return an empty list on read or JSON failures."""
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "missing.jsonl")
            malformed = os.path.join(directory, "malformed.jsonl")
            with open(malformed, "w", encoding="utf-8") as stream:
                stream.write("{not json}\n[]\nnull\n")
            self.assertEqual(common.parse_transcript_tail(missing), [])
            self.assertEqual(common.parse_transcript_tail(malformed), [])
            self.assertEqual(len(common.parse_transcript_tail(str(FIXTURE))), 3)

    def test_transcript_tail_drops_partial_first_line(self):
        """source: common.py _read_tail_lines bounds transcript reads and drops the partial first line after seeking."""
        with tempfile.TemporaryDirectory() as directory:
            transcript = os.path.join(directory, "large.jsonl")
            valid = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "final complete turn"},
            }).encode("utf-8") + b"\n"
            with open(transcript, "wb") as stream:
                stream.write(b"x" * (common._TAIL_BYTES + 1))
                stream.write(b"\n")
                stream.write(valid)
            self.assertEqual(
                common.parse_transcript_tail(transcript),
                [{"role": "user", "text": "final complete turn"}],
            )

    def test_transcript_keeps_only_last_forty_turns(self):
        """source: common.py parse_transcript_tail returns only the configured final turn window."""
        with tempfile.TemporaryDirectory() as directory:
            transcript = os.path.join(directory, "turns.jsonl")
            with open(transcript, "w", encoding="utf-8") as stream:
                for index in range(45):
                    stream.write(json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": "turn {}".format(index)},
                    }) + "\n")
            turns = common.parse_transcript_tail(transcript)
            self.assertEqual(len(turns), 40)
            self.assertEqual(turns[0]["text"], "turn 5")
            self.assertEqual(turns[-1]["text"], "turn 44")

    def test_extract_paths_preserves_first_seen_unique_order_and_limit(self):
        """source: common.py _PATH_RE and extract_paths retain the original path matcher and first-seen order."""
        text = "/work/a.py ~/docs/b.md /work/a.py /work/c.py /work/d.py"
        self.assertEqual(
            common.extract_paths(text, limit=3),
            ["/work/a.py", "~/docs/b.md", "/work/c.py"],
        )
        self.assertEqual(common.extract_paths(text, limit=0), [])


if __name__ == "__main__":
    unittest.main()
