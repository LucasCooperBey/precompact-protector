#!/usr/bin/env python3
"""Save a deterministic transcript checkpoint before Claude Code compacts."""
from __future__ import annotations

import json
import sys
from typing import Any

import common as C


def main() -> int:
    try:
        data: Any = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0

    session_id = C.safe_session_id(data.get("session_id"))
    try:
        cwd = str(data.get("cwd") or "")
        transcript_path = str(data.get("transcript_path") or "")
        trigger = str(data.get("trigger") or "")
        turns = C.parse_transcript_tail(transcript_path) if transcript_path else []
        stamp = C.checkpoint_stamp(session_id)
        checkpoint = {
            "kind": "context_event",
            "session_id": session_id,
            "cwd": cwd,
            "trigger": trigger,
            "ts": stamp,
            "transcript_path": transcript_path,
            "turn_count": len(turns),
            "turns": turns,
        }
        path = C.write_checkpoint(session_id, stamp, checkpoint)
        C.log(
            "capture: {} trigger={} turns={} -> {}".format(
                session_id, trigger, len(turns), path.rsplit("/", 1)[-1]
            )
        )
    except Exception as error:
        C.log("capture: failed: {!r}".format(error))
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        C.log("capture: unexpected failure: {!r}".format(error))
        result = 0
    sys.exit(result)
