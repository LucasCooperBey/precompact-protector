#!/usr/bin/env python3
"""Print a short context card after compaction or for a chosen session.

When the hook payload includes a transcript, the card counts compactions in
that file and quotes the last words you typed. A helper agent on another
model gets a one-line note instead. Any uncertainty still prints the card.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from typing import Any, Iterator

import common as C


MAX_CARD_CHARS = 2600
HEADER = (
    "Context protector: working memory from before this compaction. "
    "Orientation only; check specifics before relying on them."
)
TRUNCATION_MARKER = "...(truncated)"
BOUNDARY_MARKER = b'"compact_boundary"'
LAST_TYPED_STEPS = (600_000, 4_000_000, 16_000_000)
LAST_TYPED_CHARS = 600
NOT_TYPED_PREFIXES = (
    "<task-notification",
    "<local-command-stdout",
    "<local-command-stderr",
    "<local-command-caveat",
    "<bash-stdout",
    "<bash-stderr",
    "<system-reminder",
    "<system-instructions",
    "<teammate-message",
    "<cross-session-message",
    "Another Claude session sent a message",
    "This session is being continued from a previous conversation",
    "Stop hook feedback:",
    "[Request interrupted",
)
COMMAND_TAGS = ("<command-name>", "<command-message>")
TYPED_NEEDLES = ('"type":"user"', '"queued_command"')
MODEL_NEEDLES = ('"type":"assistant"', '"type":"model"', "<command-name>")
MODEL_COMMANDS = ("/model", "/fast")
HELPER_FILE_AGE_S = 900
HELPER_TAIL_BYTES = 4_000_000
HELPER_NOTE = (
    "Context protector: this compaction ran on {model} while the main chat runs "
    "{main}, so it belongs to a helper agent. The main chat's working memory is not "
    "shown; keep to your own task."
)
HELPER_CAVEAT = (
    "- A helper agent on the main chat's model also gets this card: "
    "if you are one, keep to your own task"
)
_compaction_cache: dict[str, int | None] = {}


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


def _count_boundaries(path: str) -> int | None:
    """Count compact_boundary rows in the whole file, skipping sidechain rows.

    None means the file is missing or unreadable. The row for the compaction
    that just finished is written after this hook exits, so callers add one.
    """
    if not os.path.isfile(path):
        return None
    counted = 0
    try:
        with open(path, "rb") as handle:
            for line in handle:
                if BOUNDARY_MARKER not in line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "system" or obj.get("subtype") != "compact_boundary":
                    continue
                if obj.get("isSidechain") is True:
                    continue
                counted += 1
    except OSError:
        return None
    return counted


def compaction_number(transcript_path: str) -> int | None:
    """How many compactions this transcript has had, this one included."""
    if transcript_path not in _compaction_cache:
        counted = _count_boundaries(transcript_path)
        _compaction_cache[transcript_path] = None if counted is None else counted + 1
    return _compaction_cache[transcript_path]


def _content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    if any(not isinstance(block, dict) for block in content):
        return None
    if any(block.get("type") == "tool_result" for block in content):
        return None
    parts = []
    for block in content:
        if block.get("type") != "text":
            continue
        piece = block.get("text")
        if isinstance(piece, str):
            parts.append(piece)
    return "\n".join(parts)


def _tag_inner(text: str, tag: str) -> str:
    open_tag = "<" + tag + ">"
    close_tag = "</" + tag + ">"
    start = text.find(open_tag)
    if start < 0:
        return ""
    start += len(open_tag)
    end = text.find(close_tag, start)
    if end < 0:
        return ""
    return text[start:end].strip()


def _slash_text(text: str) -> str:
    name = _tag_inner(text, "command-name")
    args = _tag_inner(text, "command-args")
    if not name:
        return ""
    if args:
        return name + " " + args
    return name


def _record_text(record: dict) -> str | None:
    """Raw text of a row that can hold what you typed, or None for any other row."""
    if record.get("isSidechain") is True:
        return None
    if record.get("type") == "user":
        if record.get("isMeta") is True or record.get("isCompactSummary") is True:
            return None
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        return _content_text(message.get("content"))
    if record.get("type") == "attachment":
        # A message typed while the agent is busy is saved only as this attachment.
        queued = record.get("attachment")
        if not isinstance(queued, dict) or queued.get("type") != "queued_command":
            return None
        if queued.get("commandMode") != "prompt":
            return None
        origin = queued.get("origin")
        if not isinstance(origin, dict) or origin.get("kind") != "human":
            return None
        return _content_text(queued.get("prompt"))
    return None


def _typed_text(record: dict) -> str | None:
    text = _record_text(record)
    if text is None:
        return None
    text = " ".join(text.split())
    if not text or text.startswith(NOT_TYPED_PREFIXES):
        return None
    if text.startswith(COMMAND_TAGS):
        return _slash_text(text) or None
    return text


def _tail_lines(path: str, max_bytes: int) -> list[bytes]:
    """Whole rows in the last max_bytes of path, split at newline bytes only.

    str.splitlines() also splits at U+2028, U+2029 and U+0085, which JSON keeps
    raw inside strings, so one pasted separator would tear a row in two. Reading
    from one byte before the window keeps a row that starts exactly at its edge.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes - 1)
                handle.readline()
            data = handle.read()
    except OSError:
        return []
    return data.split(b"\n")


def _tail_rows(path: str, max_bytes: int, needles: tuple[str, ...]) -> Iterator[dict | None]:
    """Rows from the last max_bytes, newest first, whose raw line holds a needle.

    Yields the parsed object, or None when that row does not parse as one. A last
    row with no newline after it counts whatever it holds, because a row still
    being written can be cut before its needle.
    """
    raw_needles = tuple(needle.encode("utf-8") for needle in needles)
    lines = _tail_lines(path, max_bytes)
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not any(needle in line for needle in raw_needles):
            if index < len(lines) - 1 or not line.strip():
                continue
        try:
            record = json.loads(line.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            record = None
        yield record if isinstance(record, dict) else None


def _tail_records(path: str, max_bytes: int, needles: tuple[str, ...]) -> Iterator[dict]:
    """The rows of _tail_rows that parse."""
    for record in _tail_rows(path, max_bytes, needles):
        if record is not None:
            yield record


def _last_typed_in(transcript_path: str, max_bytes: int) -> dict[str, str] | None:
    for record in _tail_records(transcript_path, max_bytes, TYPED_NEEDLES):
        text = _typed_text(record)
        if not text:
            continue
        return {
            "text": text[:LAST_TYPED_CHARS],
            "ts": str(record.get("timestamp") or ""),
        }
    return None


def _last_typed_whole_file(transcript_path: str) -> dict[str, str] | None:
    """Newest typed prompt in the whole file, one newline-terminated line at a time.

    Used when every tail step missed and the file is larger than the last step.
    A line carrying the bytes "toolUseResult" is a tool-result row and is not
    parsed. There is no size cap: the compaction count already reads the whole
    file, and a cap would hide the line this pass exists to find.
    """
    needles = tuple(needle.encode("utf-8") for needle in TYPED_NEEDLES)
    tool_mark = b'"toolUseResult"'
    newest = None
    try:
        with open(transcript_path, "rb") as handle:
            while True:
                line = handle.readline()
                if not line:
                    break
                if tool_mark in line or not any(needle in line for needle in needles):
                    continue
                try:
                    record = json.loads(line.decode("utf-8", errors="replace"))
                except (ValueError, RecursionError):
                    continue
                if not isinstance(record, dict):
                    continue
                text = _typed_text(record)
                if not text:
                    continue
                newest = {
                    "text": text[:LAST_TYPED_CHARS],
                    "ts": str(record.get("timestamp") or ""),
                }
    except OSError:
        return None
    return newest


def last_typed(transcript_path: str) -> dict[str, str] | None:
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return None
    for max_bytes in LAST_TYPED_STEPS:
        found = _last_typed_in(transcript_path, max_bytes)
        if found is not None:
            return found
        if size <= max_bytes:
            return None
    return _last_typed_whole_file(transcript_path)


def _model_id(value: object) -> str:
    """One spelling of a Claude model id, or "" for an alias or anything else."""
    if isinstance(value, dict):
        value = value.get("id")
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if value.endswith("]") and "[" in value:
        value = value[:value.rindex("[")].strip()
    return value if value.startswith("claude-") else ""


def _reply_model(record: dict) -> str | None:
    """Model id of a real reply, "" when it names no Claude model, None otherwise.

    None means the row is not a real reply: a synthetic one, a tool result, or
    any other row. "" means it is a reply whose model is an alias, a local
    model, or missing, empty, or not a string.
    """
    if record.get("type") != "assistant":
        return None
    message = record.get("message")
    model = message.get("model") if isinstance(message, dict) else None
    if model == "<synthetic>":
        return None
    return _model_id(model) if isinstance(model, str) else ""


def _model_row(record: dict) -> tuple[str, str] | None:
    """("reply" | "attachment" | "command", model id) for a main-chat model row."""
    if record.get("isSidechain") is True:
        return None
    kind = record.get("type")
    if kind == "assistant":
        model = _reply_model(record)
        return None if model is None else ("reply", model)
    if kind == "attachment":
        attachment = record.get("attachment")
        if not isinstance(attachment, dict) or attachment.get("type") != "model":
            return None
        identity = attachment.get("identity")
        model = identity.get("modelId") if isinstance(identity, dict) else None
        return ("attachment", _model_id(model))
    text = _record_text(record) if kind == "user" else None
    if text is None:
        return None
    text = " ".join(text.split())
    if text.startswith(COMMAND_TAGS) and _slash_text(text).split(" ")[0] in MODEL_COMMANDS:
        return ("command", "")
    return None


def main_models(transcript_path: str) -> set[str] | None:
    """Models named by the newest model attachment and the newest real reply.

    None when a /model or /fast row is newer than the newest model attachment:
    the switch may have no record yet. An empty set, so the owner is unknown,
    when either newest record names no usable model, when a model row in the
    window does not parse, and when a file larger than the last window holds
    only one of the two records in it.
    """
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return set()
    found: dict[str, str] = {}
    for max_bytes in LAST_TYPED_STEPS:
        found = {}
        for record in _tail_rows(transcript_path, max_bytes, MODEL_NEEDLES):
            if record is None:
                return set()
            row = _model_row(record)
            if row is None:
                continue
            kind, model = row
            if kind == "command":
                if "attachment" not in found:
                    return None
                continue
            if kind in found:
                continue
            if not model:
                return set()
            found[kind] = model
            if len(found) == 2:
                break
        if len(found) == 2 or size <= max_bytes:
            break
    else:
        return set()
    return set(found.values())


def helper_has_model(transcript_path: str, model: str, now: float) -> bool:
    """True when a recent helper file holds a reply on model.

    Only a reply row's own model counts. A model named in a tool call, a tool
    result, or a request row is data about some other agent. A helper's rows
    for this compaction reach its file only after the hook exits, so the check
    uses earlier replies in files written within the last 15 minutes.
    """
    if not model or not transcript_path:
        return False
    folder = os.path.join(glob.escape(os.path.splitext(transcript_path)[0]), "subagents")
    for path in glob.glob(os.path.join(folder, "**", "*.jsonl"), recursive=True):
        try:
            if now - os.path.getmtime(path) > HELPER_FILE_AGE_S:
                continue
        except OSError:
            continue
        for record in _tail_records(path, HELPER_TAIL_BYTES, (model,)):
            if _reply_model(record) == model:
                return True
    return False


def compaction_owner(
    transcript_path: str, payload_model: object, now: float
) -> tuple[str, set[str] | None]:
    """Whose compaction this is: "main", "helper", "unknown", or "unconfirmed".

    Only "helper" replaces the card with the note. The payload model must
    differ from every model the main chat's records name, and a fresh helper
    file must hold a reply on it. The main chat's models are returned for the log.
    """
    model = _model_id(payload_model)
    main = main_models(transcript_path)
    if not model or not main:
        return "unknown", main
    if model in main:
        return "main", main
    if helper_has_model(transcript_path, model, now):
        return "helper", main
    return "unconfirmed", main


def _models_text(main: set[str] | None) -> str:
    if main is None:
        return "switching"
    return ",".join(sorted(main)) or "none"


def _checkpoint_last_user(checkpoint: dict[str, Any]) -> str:
    turns_value = checkpoint.get("turns")
    turns = turns_value if isinstance(turns_value, list) else []
    for turn in reversed(turns):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        user_text = turn.get("text")
        if isinstance(user_text, str):
            return user_text[:600].strip()
        return ""
    return ""


def _append_checkpoint_lines(output: list[str], checkpoint: dict[str, Any]) -> None:
    turns_value = checkpoint.get("turns")
    turns = turns_value if isinstance(turns_value, list) else []
    valid_turns = [turn for turn in turns if isinstance(turn, dict)]
    recent_text = "\n".join(
        turn.get("text", "")
        for turn in valid_turns[-12:]
        if isinstance(turn.get("text", ""), str)
    )
    paths = C.extract_paths(recent_text, limit=8)
    if paths:
        output.append("- Files in play: " + ", ".join(paths))
    count = checkpoint.get("turn_count", len(valid_turns))
    timestamp = checkpoint.get("ts", "")
    output.append("- {} turns checkpointed at {}".format(count, timestamp))


def _floor_card(session_id: object, transcript_path: str = "") -> str:
    checkpoint = _latest_checkpoint(session_id)
    has_transcript = bool(transcript_path) and os.path.isfile(transcript_path)
    if not checkpoint and not has_transcript:
        return ""
    output = ["## Before compaction (raw floor)"]
    if has_transcript:
        number = compaction_number(transcript_path)
        if number is not None:
            output.append(
                "- Compaction {} of this transcript, this one included".format(number)
            )
        output.append(HELPER_CAVEAT)
        typed = last_typed(transcript_path)
        if typed is None:
            output.append("- Last words you typed: none found in the transcript")
        elif typed.get("ts"):
            output.append(
                "- Last words you typed ({}): {}".format(typed["ts"], typed["text"])
            )
        else:
            output.append("- Last words you typed: {}".format(typed["text"]))
    elif checkpoint:
        last_user = _checkpoint_last_user(checkpoint)
        if last_user:
            output.append("- Last user intent: " + last_user)
    if checkpoint:
        _append_checkpoint_lines(output, checkpoint)
    return "\n".join(output)


def build_card(session_id: object, transcript_path: str = "") -> str:
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
    floor = _floor_card(session_id, transcript_path)
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


def _emit(text: str) -> None:
    """Write text and a newline. A lone surrogate becomes U+FFFD so the write succeeds."""
    safe = "".join(chr(0xFFFD) if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)
    sys.stdout.write(safe + "\n")


def _log_value(value: object, limit: int = 80) -> str:
    text = str(value if value else "none")
    text = text.replace("\n", " ").replace("\r", " ")
    return text[:limit]


def _log_run(
    prefix: str,
    length: int,
    compaction: object,
    owner: str,
    model: str,
    prompt: str,
    main: set[str] | None,
) -> None:
    C.log(
        "rehydrate: {} ({} chars) compaction={} owner={} model={} prompt={} main={}".format(
            prefix,
            length,
            compaction,
            owner,
            model,
            prompt,
            _models_text(main),
        )
    )


def _payload_labels(data: dict) -> tuple[str, str]:
    model = data.get("model")
    if isinstance(model, dict):
        model = model.get("id")
    return _log_value(model), _log_value(data.get("prompt_id"))


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
        _inject_compact(data, str(session_id))
    except Exception:
        return 0
    return 0


def _inject_compact(data: dict, session_id: str) -> None:
    transcript_path = str(data.get("transcript_path") or "")
    model, prompt = _payload_labels(data)
    try:
        owner, main = compaction_owner(transcript_path, data.get("model"), time.time())
    except Exception as error:
        owner, main = "error:" + type(error).__name__, None
    if owner == "helper" and main:
        note = HELPER_NOTE.format(
            model=_model_id(data.get("model")),
            main=" or ".join(sorted(main)),
        )
        _emit(note)
        _log_run(
            "helper compaction for {}".format(session_id),
            len(note),
            "none",
            "helper",
            model,
            prompt,
            main,
        )
        return
    card = build_card(session_id, transcript_path)
    if not card:
        return
    _emit(card)
    number = compaction_number(transcript_path) if transcript_path else None
    shown = "none" if number is None else number
    _log_run(
        "injected card for {}".format(session_id),
        len(card),
        shown,
        owner,
        model,
        prompt,
        main,
    )


def manual(session_id: str) -> int:
    card = build_card(session_id)
    if not _latest_checkpoint(session_id):
        sys.stderr.write("no checkpoint for {}\n".format(session_id))
        return 1
    if not card:
        sys.stderr.write("no checkpoint for {}\n".format(session_id))
        return 1
    _emit(card)
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
