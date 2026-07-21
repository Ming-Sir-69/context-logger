import json
import os
from pathlib import Path

from ..models import SourceSession


CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


def canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def read_meta(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "session_meta":
                return entry.get("payload", {})
    raise ValueError(f"Codex Session 缺少 session_meta: {path}")


def iter_sessions(base: Path = CODEX_SESSIONS_DIR):
    if not base.is_dir():
        return
    yield from base.glob("**/rollout-*.jsonl")


def find_by_id(session_id: str, base: Path = CODEX_SESSIONS_DIR) -> Path:
    for path in iter_sessions(base) or ():
        meta = read_meta(path)
        if meta.get("parent_thread_id"):
            continue
        if meta.get("id") == session_id:
            return path
    raise ValueError(f"未找到 Codex Session: {session_id}")


def resolve(
    session_id: str | None = None,
    session_file: Path | None = None,
) -> SourceSession:
    if session_file is not None:
        path = canonical(session_file)
        confidence = "explicit_session_file"
    else:
        resolved_id = session_id or os.environ.get("CODEX_THREAD_ID")
        if not resolved_id:
            raise ValueError("Codex 需要 --session-id、CODEX_THREAD_ID 或 --session-file")
        path = find_by_id(resolved_id)
        confidence = "exact_session"

    meta = read_meta(path)
    resolved_id = meta.get("id") or meta.get("session_id")
    workspace = meta.get("cwd")
    if not resolved_id or not workspace:
        raise ValueError(f"Codex Session 身份不完整: {path}")
    return SourceSession(
        source="codex",
        session_id=resolved_id,
        session_path=path,
        workspace_root=canonical(workspace),
        confidence=confidence,
        completeness="complete",
    )
