"""Shared paths, logging, transcript parsing, and checkpoint writing."""
from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
from typing import Any


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TAIL_BYTES = 600_000
_PATH_RE = re.compile(r"(?:/[\w.\-]+){2,}\.\w+|~/[\w./\-]+")


def data_root() -> str:
    """Return the configured data root, expanding a leading tilde."""
    configured = os.environ.get("PRECOMPACT_DIR")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(os.path.expanduser("~"), ".claude", "precompact")


def safe_session_id(session_id: object) -> str:
    """Return a safe single-component session name, or ``unknown``."""
    try:
        value = str(session_id) if session_id is not None else ""
    except Exception:
        return "unknown"
    if value in (".", "..") or not _SESSION_ID_RE.fullmatch(value):
        return "unknown"
    return value


def _private_directory(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def ensure_dirs() -> tuple[str, str]:
    """Create private root and sessions directories and return both paths."""
    root = data_root()
    sessions = os.path.join(root, "sessions")
    _private_directory(root)
    if os.path.islink(sessions):
        raise OSError("sessions directory must not be a symbolic link")
    _private_directory(sessions)
    return root, sessions


def session_dir(session_id: object) -> str:
    """Return a private, contained directory for a validated session id."""
    _, sessions = ensure_dirs()
    name = safe_session_id(session_id)
    directory = os.path.join(sessions, name)
    if os.path.islink(directory):
        raise OSError("session directory must not be a symbolic link")
    _private_directory(directory)
    return directory


def now_ts() -> str:
    """Return a UTC timestamp with microseconds for unique checkpoint names."""
    current = datetime.datetime.now(datetime.timezone.utc)
    return current.strftime("%Y%m%dT%H%M%SZ-") + f"{current.microsecond:06d}"


def checkpoint_stamp(session_id: object) -> str:
    """Choose an unused timestamp in the session directory."""
    directory = session_dir(session_id)
    stamp = now_ts()
    while os.path.exists(os.path.join(directory, "checkpoint-{}.json".format(stamp))):
        parsed = datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ-%f")
        parsed = parsed.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(
            microseconds=1
        )
        stamp = parsed.strftime("%Y%m%dT%H%M%SZ-") + f"{parsed.microsecond:06d}"
    return stamp


def log(message: str) -> None:
    """Append a best-effort log line to the private data root."""
    try:
        root, _ = ensure_dirs()
        path = os.path.join(root, "protector.log")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                descriptor = -1
                current = datetime.datetime.now(datetime.timezone.utc)
                stream.write(current.strftime("%Y-%m-%dT%H:%M:%SZ") + " | " + message + "\n")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except Exception:
        return


def write_checkpoint(session_id: object, stamp: str, checkpoint: dict[str, Any]) -> str:
    """Atomically write a private checkpoint in its session directory."""
    directory = session_dir(session_id)
    destination = os.path.join(directory, "checkpoint-{}.json".format(stamp))
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".checkpoint-", suffix=".tmp", dir=directory
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(checkpoint, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
        os.replace(temporary_path, destination)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise
    return destination


def _read_tail_lines(path: str, max_bytes: int = _TAIL_BYTES) -> list[str]:
    """Read at most the final byte window and discard a partial leading line."""
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            seeking = size > max_bytes
            if seeking:
                stream.seek(size - max_bytes)
            else:
                stream.seek(0)
            data = stream.read(max_bytes)
    except Exception:
        return []
    if seeking:
        newline = data.find(b"\n")
        data = data[newline + 1:] if newline >= 0 else b""
    return data.decode("utf-8", errors="replace").splitlines()


def _flatten_content(content: Any) -> str:
    """Flatten message content using the original deterministic block format."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "thinking":
            thinking = str(block.get("thinking", ""))
            if thinking:
                parts.append("[thinking] " + thinking)
        elif block_type == "tool_use":
            name = block.get("name", "tool")
            input_text = json.dumps(block.get("input", {}), ensure_ascii=False)
            if len(input_text) > 400:
                input_text = input_text[:400] + "…"
            parts.append("[tool_use:{}] {}".format(name, input_text))
        elif block_type == "tool_result":
            content_value = block.get("content", "")
            content_text = (
                content_value
                if isinstance(content_value, str)
                else json.dumps(content_value, ensure_ascii=False)
            )
            if len(content_text) > 400:
                content_text = content_text[:400] + "…"
            parts.append("[tool_result] " + content_text)
    return "\n".join(part for part in parts if part)


def parse_transcript_tail(path: str, max_turns: int = 40) -> list[dict[str, str]]:
    """Return the last user and assistant turns from a transcript JSONL file."""
    turns: list[dict[str, str]] = []
    try:
        for line in _read_tail_lines(path):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") not in ("user", "assistant"):
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            text = _flatten_content(message.get("content"))
            if not text.strip():
                continue
            turns.append({"role": str(message.get("role") or event.get("type")), "text": text})
    except Exception:
        return []
    if max_turns <= 0:
        return []
    return turns[-max_turns:]


def extract_paths(text: str, limit: int = 8) -> list[str]:
    """Extract distinct file paths in first-seen order using the original matcher."""
    if limit <= 0:
        return []
    paths: list[str] = []
    for match in _PATH_RE.findall(text):
        if match not in paths:
            paths.append(match)
        if len(paths) >= limit:
            break
    return paths
