import hashlib
import json
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

from ..models import SourceSession


DEFAULT_ANCHORS_DIR = (
    Path.home()
    / ".cc-switch"
    / "runtime"
    / "context-logger"
    / "anchors"
)
ANCHOR_MAX_AGE_SECONDS = 24 * 60 * 60
ANCHOR_FUTURE_TOLERANCE_SECONDS = 5 * 60


def canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def anchor_directory(path: Path | None = None) -> Path:
    configured = path or os.environ.get("CONTEXT_LOGGER_ANCHORS_DIR")
    return canonical(configured or DEFAULT_ANCHORS_DIR)


def anchor_name(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", session_id).strip("._")
    if not safe:
        raise ValueError("Claude Code session_id 无法形成安全锚点名称")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"{safe[:80]}-{digest}.json"


def atomic_write_anchor(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.chmod(0o600)
    os.replace(temp_path, path)


def write_anchor(payload: dict, anchors_dir: Path | None = None) -> Path:
    if payload.get("hook_event_name") != "SessionStart":
        raise ValueError("anchor 只接受 Claude Code SessionStart 输入")
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    workspace_root = payload.get("cwd")
    if not all(
        isinstance(value, str) and value
        for value in (session_id, transcript_path, workspace_root)
    ):
        raise ValueError(
            "SessionStart 输入缺少 session_id、transcript_path 或 cwd"
        )
    transcript = canonical(transcript_path)
    workspace = canonical(workspace_root)
    if transcript.suffix != ".jsonl":
        raise ValueError("Claude Code transcript_path 必须为 .jsonl")
    if not workspace.is_dir():
        raise ValueError(f"Claude Code cwd 目录不存在: {workspace}")
    anchor = {
        "schema_version": 1,
        "source": "claude-code",
        "session_id": session_id,
        "transcript_path": str(transcript),
        "workspace_root": str(workspace),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = anchor_directory(anchors_dir) / anchor_name(session_id)
    atomic_write_anchor(path, anchor)

    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        env_path = canonical(env_file)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "export CONTEXT_LOGGER_CLAUDE_SESSION_ID="
                f"{shlex.quote(session_id)}\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    return path


def load_anchor(
    session_id: str,
    anchors_dir: Path | None = None,
) -> dict:
    path = anchor_directory(anchors_dir) / anchor_name(session_id)
    if not path.is_file():
        raise ValueError(f"Claude Code 锚点不存在: {session_id}")
    with path.open("r", encoding="utf-8") as handle:
        anchor = json.load(handle)
    if anchor.get("schema_version") != 1:
        raise ValueError(f"Claude Code 锚点版本无效: {path}")
    if (
        anchor.get("source") != "claude-code"
        or anchor.get("session_id") != session_id
    ):
        raise ValueError(f"Claude Code 锚点身份冲突: {path}")
    try:
        updated_at = datetime.fromisoformat(
            str(anchor["updated_at"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as error:
        raise ValueError(f"Claude Code 锚点时间无效: {path}") from error
    if updated_at.tzinfo is None:
        raise ValueError(f"Claude Code 锚点时间缺少时区: {path}")
    age = (
        datetime.now(timezone.utc)
        - updated_at.astimezone(timezone.utc)
    ).total_seconds()
    if age > ANCHOR_MAX_AGE_SECONDS:
        raise ValueError(f"Claude Code 锚点已过期: {path}")
    if age < -ANCHOR_FUTURE_TOLERANCE_SECONDS:
        raise ValueError(f"Claude Code 锚点时间位于未来: {path}")
    return anchor


def resolve_session_file(path: Path, confidence: str) -> SourceSession:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = entry.get("sessionId")
            workspace = entry.get("cwd")
            if session_id and workspace:
                return SourceSession(
                    source="claude-code",
                    session_id=session_id,
                    session_path=path,
                    workspace_root=canonical(workspace),
                    confidence=confidence,
                    completeness="complete",
                )
    raise ValueError(f"Claude Code Session 身份不完整: {path}")


def resolve(
    session_file: Path | None = None,
    session_id: str | None = None,
    anchors_dir: Path | None = None,
) -> SourceSession:
    if session_file is not None:
        return resolve_session_file(
            canonical(session_file),
            "explicit_session_file",
        )
    resolved_id = (
        session_id
        or os.environ.get("CONTEXT_LOGGER_CLAUDE_SESSION_ID")
    )
    if not resolved_id:
        raise ValueError(
            "Claude Code 需要 --session-id、Hook 环境锚点或 --session-file"
        )
    anchor = load_anchor(resolved_id, anchors_dir)
    transcript_path = canonical(anchor["transcript_path"])
    resolved = resolve_session_file(transcript_path, "exact_session")
    if resolved.session_id != resolved_id:
        raise ValueError(
            "Claude Code 锚点与 Transcript Session ID 冲突"
        )
    anchor_workspace = canonical(anchor["workspace_root"])
    if resolved.workspace_root != anchor_workspace:
        raise ValueError(
            "Claude Code 锚点与 Transcript 工作区冲突"
        )
    return resolved
