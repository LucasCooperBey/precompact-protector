#!/usr/bin/env python3
"""Print a short context card after compaction or for a chosen session."""
from __future__ import annotations

import glob
import json
import os
import sys
from typing import Any

import common as C


MAX_CARD_CHARS = 2600
HEADER = (
    "Context protector: working memory from before this compaction. "
    "Orientation only; check specifics before relying on them."
)
TRUNCATION_MARKER = "...(truncated)"


def _latest_checkpoint(session_id: object) -> dict[str, Any] | None:
    try:
        directory = C.session_dir(session_id)
        files = sorted(glob.glob(os.path.join(directory, "checkpoint-*.json")))
    except Exception:
        return None
    if not files:
        return None
    try:
        with open(files[-1], "r", encoding="utf-8") as stream:
            checkpoint = json.load(stream)
        return checkpoint if isinstance(checkpoint, dict) else None
    except Exception:
        return None


def _floor_card(checkpoint: dict[str, Any] | None) -> str:
    if not checkpoint:
        return ""
    turns_value = checkpoint.get("turns")
    turns = turns_value if isinstance(turns_value, list) else []
    valid_turns = [turn for turn in turns if isinstance(turn, dict)]
    last_user = ""
    for turn in reversed(valid_turns):
        if turn.get("role") == "user":
            user_text = turn.get("text")
            if isinstance(user_text, str):
                last_user = user_text[:600].strip()
            break
    recent_text = "\n".join(
        turn.get("text", "")
        for turn in valid_turns[-12:]
        if isinstance(turn.get("text", ""), str)
    )
    paths = C.extract_paths(recent_text, limit=8)
    output = ["## Before compaction (raw floor)"]
    if last_user:
        output.append("- Last user intent: " + last_user)
    if paths:
        output.append("- Files in play: " + ", ".join(paths))
    count = checkpoint.get("turn_count", len(valid_turns))
    timestamp = checkpoint.get("ts", "")
    output.append("- {} turns checkpointed at {}".format(count, timestamp))
    return "\n".join(output)


def build_card(session_id: object) -> str:
    """Build the bounded card from an optional user card and newest checkpoint."""
    parts: list[str] = []
    try:
        card_path = os.path.join(C.session_dir(session_id), "REHYDRATE.md")
        if os.path.isfile(card_path):
            with open(card_path, "r", encoding="utf-8") as stream:
                user_card = stream.read().strip()
            if user_card:
                parts.append(user_card)
    except Exception:
        pass
    floor = _floor_card(_latest_checkpoint(session_id))
    if floor:
        parts.append(floor)
    if not parts:
        return ""

    card = HEADER + "\n\n" + "\n\n".join(parts)
    if len(card) > MAX_CARD_CHARS:
        body_budget = MAX_CARD_CHARS - len(HEADER) - 2 - len(TRUNCATION_MARKER)
        body = card[len(HEADER) + 2:]
        card = HEADER + "\n\n" + body[:body_budget] + TRUNCATION_MARKER
    return card


def inject() -> int:
    try:
        data: Any = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict) or data.get("source") != "compact":
        return 0
    session_id = data.get("session_id")
    if session_id is None or str(session_id) == "":
        return 0
    try:
        card = build_card(session_id)
        if card:
            sys.stdout.write(card + "\n")
    except Exception:
        return 0
    return 0


def manual(session_id: str) -> int:
    card = build_card(session_id)
    if not _latest_checkpoint(session_id):
        sys.stderr.write("no checkpoint for {}\n".format(session_id))
        return 1
    if not card:
        sys.stderr.write("no checkpoint for {}\n".format(session_id))
        return 1
    sys.stdout.write(card + "\n")
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--session":
        return manual(sys.argv[2])
    return inject()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
