import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .index import ContextIndex, read_jsonl, scan_raw_entries
from .markdown import (
    MAX_CHARS,
    atomic_write_text,
    canonical_chunk_paths,
    reconstruct_message_text,
    render_module_index,
    render_session,
)
from .models import NormalizedEvent, Resolution
from .normalize import normalize_raw_part
from .raw_store import RawArchiveResult, archive_raw


class UnsupportedSourceError(RuntimeError):
    pass


class RawWriteError(RuntimeError):
    pass


class DerivedLayerError(RuntimeError):
    def __init__(self, message: str, raw_result: RawArchiveResult):
        super().__init__(message)
        self.raw_result = raw_result


@dataclass(frozen=True)
class SaveResult:
    source: str
    session_id: str
    session_dir: Path
    target_dir: Path
    new_raw_entries: int
    total_raw_entries: int
    normalized_events: int
    chunks: int
    derived_health: str


@dataclass(frozen=True)
class VerifyResult:
    verified: bool
    issues: list[str]
    sessions: int
    raw_entries: int
    events: int
    chunks: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def atomic_write_jsonl(path: Path, values: list[dict]) -> None:
    content = "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
        for value in values
    )
    atomic_write_text(path, content)


def read_json_or(path: Path, default: dict) -> dict:
    if not path.is_file():
        return dict(default)
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def safe_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if not safe:
        raise ValueError("Session 目录名称为空")
    return safe


def session_directory(target_dir: Path, source: str, session_id: str) -> Path:
    return (
        Path(target_dir).expanduser().resolve()
        / "sessions"
        / f"{safe_component(source)}_{safe_component(session_id)}"
    )


def manifest_for(
    resolution: Resolution,
    raw_entries: list[dict],
) -> dict:
    return {
        "schema_version": 2,
        "source": resolution.source,
        "session_id": resolution.session_id,
        "session_path": (
            str(resolution.session_path)
            if resolution.session_path is not None
            else ""
        ),
        "workspace_root": str(resolution.workspace_root),
        "target_dir": str(resolution.target_dir),
        "confidence": resolution.confidence,
        "completeness": resolution.completeness,
        "target_overridden": resolution.target_overridden,
        "module_id": resolution.module_id,
        "raw_entries": raw_entries,
    }


def resolution_from_manifest(
    manifest: dict,
    target_dir: Path,
) -> Resolution:
    return Resolution(
        source=str(manifest["source"]),
        session_id=str(manifest["session_id"]),
        session_path=(
            Path(manifest["session_path"]).expanduser().resolve()
            if manifest.get("session_path")
            else None
        ),
        workspace_root=Path(
            manifest.get("workspace_root") or target_dir
        ).expanduser().resolve(),
        target_dir=Path(target_dir).expanduser().resolve(),
        confidence=str(manifest.get("confidence") or "unsupported"),
        completeness=str(manifest.get("completeness") or "unsupported"),
        target_overridden=bool(manifest.get("target_overridden")),
        module_id=manifest.get("module_id"),
    )


def complete_raw_entries(
    session_dir: Path,
    previous: list[dict],
    new_records: list[dict],
) -> list[dict]:
    metadata = {}
    for entry in [*previous, *new_records]:
        key = (str(entry.get("part") or ""), int(entry.get("line") or 0))
        metadata[key] = dict(entry)
    complete = []
    for actual in scan_raw_entries(session_dir) or ():
        key = (actual["part"], actual["line"])
        entry = dict(metadata.get(key, {}))
        entry.update(actual)
        complete.append(entry)
    return complete


def normalize_session(
    source: str,
    session_id: str,
    session_dir: Path,
) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    next_ordinal = 1
    seen_event_ids: set[str] = set()
    for part_path in sorted((session_dir / "raw").glob("part-*.jsonl")):
        part_events = normalize_raw_part(
            source,
            session_id,
            part_path,
            start_ordinal=next_ordinal,
        )
        for event in part_events:
            if event.event_id in seen_event_ids:
                continue
            seen_event_ids.add(event.event_id)
            events.append(event)
        next_ordinal += len(part_path.read_bytes().splitlines())
    return events


def write_normalized(
    events: list[NormalizedEvent],
    session_dir: Path,
) -> Path:
    normalized_dir = session_dir / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    path = normalized_dir / "events-000001.jsonl"
    atomic_write_jsonl(path, [event.to_dict() for event in events])
    for stale in normalized_dir.glob("events-*.jsonl"):
        if stale != path:
            stale.unlink()
    return path


def ready_state(
    raw_state: dict,
    event_count: int,
    chunk_count: int,
) -> dict:
    return {
        "schema_version": 2,
        "raw_state": raw_state,
        "derived_health": "ready",
        "event_count": event_count,
        "chunk_count": chunk_count,
        "last_error": "",
        "last_saved_at": now_iso(),
    }


def failed_state(raw_state: dict, error: Exception) -> dict:
    return {
        "schema_version": 2,
        "raw_state": raw_state,
        "derived_health": "needs_rebuild",
        "last_error": str(error),
        "last_saved_at": now_iso(),
    }


def save_context(resolution: Resolution) -> SaveResult:
    if resolution.completeness != "complete":
        raise UnsupportedSourceError(
            f"completeness={resolution.completeness}; "
            f"source={resolution.source}"
        )
    if resolution.session_path is None:
        raise UnsupportedSourceError(
            f"completeness=unsupported; source={resolution.source}"
        )

    target_dir = resolution.target_dir.expanduser().resolve()
    session_dir = session_directory(
        target_dir,
        resolution.source,
        resolution.session_id,
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    state_path = session_dir / "state.json"
    manifest_path = session_dir / "manifest.json"
    previous_state = read_json_or(state_path, {})
    previous_manifest = read_json_or(manifest_path, {})
    if previous_manifest and (
        previous_manifest.get("source") != resolution.source
        or previous_manifest.get("session_id") != resolution.session_id
    ):
        raise ValueError(
            "Session 目录身份冲突，拒绝复用: "
            f"{session_dir}"
        )

    try:
        raw_result = archive_raw(
            resolution.session_path,
            session_dir,
            previous_state.get("raw_state", {}),
        )
    except Exception as error:
        raise RawWriteError(str(error)) from error

    raw_entries = complete_raw_entries(
        session_dir,
        list(previous_manifest.get("raw_entries") or ()),
        raw_result.records,
    )
    try:
        atomic_write_json(
            manifest_path,
            manifest_for(resolution, raw_entries),
        )
    except Exception as error:
        raise RawWriteError(
            f"Raw 已写入但 Manifest 提交失败: {error}"
        ) from error

    try:
        events = normalize_session(
            resolution.source,
            resolution.session_id,
            session_dir,
        )
        write_normalized(events, session_dir)
        chunk_paths = render_session(events, session_dir, resolution)
        render_module_index(target_dir)
        state = ready_state(
            raw_result.state,
            len(events),
            len(chunk_paths),
        )
        ContextIndex(
            target_dir / "index" / "context.sqlite3"
        ).rebuild(
            target_dir,
            state_overrides={resolution.session_id: state},
        )
        atomic_write_json(state_path, state)
    except Exception as error:
        state = failed_state(raw_result.state, error)
        atomic_write_json(state_path, state)
        raise DerivedLayerError(str(error), raw_result) from error

    return SaveResult(
        source=resolution.source,
        session_id=resolution.session_id,
        session_dir=session_dir,
        target_dir=target_dir,
        new_raw_entries=raw_result.new_entries,
        total_raw_entries=len(raw_entries),
        normalized_events=len(events),
        chunks=len(chunk_paths),
        derived_health="ready",
    )


def session_dirs(target_dir: Path):
    sessions_dir = Path(target_dir).expanduser().resolve() / "sessions"
    if not sessions_dir.is_dir():
        return
    yield from sorted(
        path for path in sessions_dir.iterdir() if path.is_dir()
    )


def find_session(
    target_dir: Path,
    session_id: str,
) -> tuple[Path, dict]:
    matches = []
    for session_dir in session_dirs(target_dir) or ():
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = read_json_or(manifest_path, {})
        if manifest.get("session_id") == session_id:
            matches.append((session_dir, manifest))
    if len(matches) != 1:
        raise ValueError(
            f"未找到唯一 Session: {session_id}; matches={len(matches)}"
        )
    return matches[0]


def rebuild_archive(target_dir: Path) -> dict:
    target_dir = Path(target_dir).expanduser().resolve()
    pending_states: dict[str, dict] = {}
    session_paths: dict[str, Path] = {}
    total_events = 0
    total_chunks = 0
    for session_dir in session_dirs(target_dir) or ():
        manifest = read_json_or(session_dir / "manifest.json", {})
        resolution = resolution_from_manifest(manifest, target_dir)
        events = normalize_session(
            resolution.source,
            resolution.session_id,
            session_dir,
        )
        write_normalized(events, session_dir)
        chunks = render_session(events, session_dir, resolution)
        prior_state = read_json_or(session_dir / "state.json", {})
        state = ready_state(
            prior_state.get("raw_state", {}),
            len(events),
            len(chunks),
        )
        pending_states[resolution.session_id] = state
        session_paths[resolution.session_id] = session_dir / "state.json"
        total_events += len(events)
        total_chunks += len(chunks)
    render_module_index(target_dir)
    ContextIndex(
        target_dir / "index" / "context.sqlite3"
    ).rebuild(target_dir, state_overrides=pending_states)
    for session_id, state in pending_states.items():
        atomic_write_json(session_paths[session_id], state)
    return {
        "sessions": len(pending_states),
        "events": total_events,
        "chunks": total_chunks,
    }


def verify_archive(
    target_dir: Path,
    session_id: str | None = None,
) -> VerifyResult:
    target_dir = Path(target_dir).expanduser().resolve()
    issues: list[str] = []
    session_count = raw_count = event_count = chunk_count = 0
    normalized_ids: dict[str, set[str]] = {}
    raw_values: dict[str, dict[tuple[str, int], str]] = {}
    chunk_values: dict[str, dict[str, tuple[int, str]]] = {}
    state_values: dict[str, dict] = {}
    candidates = []
    if session_id:
        candidates.append(find_session(target_dir, session_id)[0])
    else:
        candidates.extend(session_dirs(target_dir) or ())

    for session_dir in candidates:
        session_count += 1
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.is_file():
            issues.append(f"{session_dir.name}: missing manifest.json")
            continue
        manifest = read_json_or(manifest_path, {})
        current_id = str(manifest.get("session_id") or "")
        expected_raw = {
            (str(entry.get("part")), int(entry.get("line") or 0)): str(
                entry.get("sha256") or ""
            )
            for entry in manifest.get("raw_entries") or ()
        }
        actual_raw = {
            (entry["part"], entry["line"]): entry["sha256"]
            for entry in scan_raw_entries(session_dir) or ()
        }
        raw_values[current_id] = actual_raw
        raw_count += len(actual_raw)
        if expected_raw != actual_raw:
            issues.append(f"{current_id}: Raw Manifest 与文件不一致")

        events = []
        for path in sorted(
            (session_dir / "normalized").glob("events-*.jsonl")
        ):
            events.extend(read_jsonl(path))
        event_count += len(events)
        ids = {str(event.get("event_id") or "") for event in events}
        normalized_ids[current_id] = ids
        if len(ids) != len(events):
            issues.append(f"{current_id}: Normalized event_id 重复")
        for event in events:
            raw_ref = event.get("raw_ref") or {}
            key = (
                str(raw_ref.get("part") or ""),
                int(raw_ref.get("line") or 0),
            )
            if actual_raw.get(key) != raw_ref.get("sha256"):
                issues.append(
                    f"{current_id}: event {event.get('event_id')} Raw 引用无效"
                )
            if (
                event.get("category") == "message"
                and event.get("role") in ("user", "assistant")
            ):
                chunk_paths = canonical_chunk_paths(
                    session_dir / "context"
                )
                reconstructed = reconstruct_message_text(
                    chunk_paths,
                    str(event.get("event_id") or ""),
                )
                if reconstructed != str(event.get("text") or ""):
                    issues.append(
                        f"{current_id}: event {event.get('event_id')} "
                        "Markdown 正文不完整"
                    )

        current_chunks: dict[str, tuple[int, str]] = {}
        for chunk_path in canonical_chunk_paths(
            session_dir / "context"
        ):
            chunk_count += 1
            chunk_text = chunk_path.read_text(encoding="utf-8")
            if len(chunk_text) > MAX_CHARS:
                issues.append(f"{current_id}: {chunk_path.name} 超过硬上限")
            current_chunks[str(chunk_path.relative_to(session_dir))] = (
                len(chunk_text),
                hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            )
        chunk_values[current_id] = current_chunks
        if not (session_dir / "INDEX.md").is_file():
            issues.append(f"{current_id}: missing Session INDEX.md")
        state = read_json_or(session_dir / "state.json", {})
        state_values[current_id] = state
        if state.get("derived_health") != "ready":
            issues.append(f"{current_id}: derived_health 非 ready")
        if state.get("event_count") != len(events):
            issues.append(f"{current_id}: state event_count 不一致")
        if state.get("chunk_count") != len(current_chunks):
            issues.append(f"{current_id}: state chunk_count 不一致")

    module_index = target_dir / "INDEX.md"
    if session_count == 0:
        issues.append("没有可核验的 Session")
    if session_count and not module_index.is_file():
        issues.append("missing Module INDEX.md")

    db_path = target_dir / "index" / "context.sqlite3"
    if session_count and not db_path.is_file():
        issues.append("missing SQLite index")
    elif db_path.is_file():
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                integrity = connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()
                if not integrity or integrity[0] != "ok":
                    issues.append("SQLite integrity_check 失败")
                db_sessions = {
                    row[0]
                    for row in connection.execute(
                        "SELECT session_id FROM sessions"
                    )
                }
                if db_sessions != set(normalized_ids):
                    issues.append("SQLite sessions 集合不一致")
                db_ids: dict[str, set[str]] = {}
                for current_id, event_id in connection.execute(
                    "SELECT session_id, event_id FROM events"
                ):
                    db_ids.setdefault(current_id, set()).add(event_id)
                for current_id, expected_ids in normalized_ids.items():
                    if db_ids.get(current_id, set()) != expected_ids:
                        issues.append(
                            f"{current_id}: SQLite event IDs 不一致"
                        )
                db_raw: dict[
                    str,
                    dict[tuple[str, int], str],
                ] = {}
                for current_id, part, line, sha256 in connection.execute(
                    "SELECT session_id, part, line, sha256 FROM raw_entries"
                ):
                    db_raw.setdefault(current_id, {})[(part, line)] = sha256
                for current_id, expected_raw in raw_values.items():
                    if db_raw.get(current_id, {}) != expected_raw:
                        issues.append(
                            f"{current_id}: SQLite raw_entries 不一致"
                        )
                db_chunks: dict[
                    str,
                    dict[str, tuple[int, str]],
                ] = {}
                for (
                    current_id,
                    chunk_path,
                    char_count_value,
                    sha256,
                ) in connection.execute(
                    "SELECT session_id, chunk_path, char_count, sha256 "
                    "FROM chunks"
                ):
                    db_chunks.setdefault(current_id, {})[chunk_path] = (
                        char_count_value,
                        sha256,
                    )
                for current_id, expected_chunks in chunk_values.items():
                    if db_chunks.get(current_id, {}) != expected_chunks:
                        issues.append(
                            f"{current_id}: SQLite chunks 不一致"
                        )
                for current_id, state_json in connection.execute(
                    "SELECT session_id, state_json FROM archive_state"
                ):
                    if (
                        current_id in state_values
                        and json.loads(state_json) != state_values[current_id]
                    ):
                        issues.append(
                            f"{current_id}: SQLite archive_state 不一致"
                        )
        except sqlite3.Error as error:
            issues.append(f"SQLite 读取失败: {error}")

    return VerifyResult(
        verified=not issues,
        issues=issues,
        sessions=session_count,
        raw_entries=raw_count,
        events=event_count,
        chunks=chunk_count,
    )


def archive_status(target_dir: Path) -> dict:
    target_dir = Path(target_dir).expanduser().resolve()
    sessions = 0
    ready = 0
    needs_rebuild = 0
    for session_dir in session_dirs(target_dir) or ():
        sessions += 1
        state = read_json_or(session_dir / "state.json", {})
        if state.get("derived_health") == "ready":
            ready += 1
        else:
            needs_rebuild += 1
    legacy_layout = any(
        (
            (target_dir / "raw").is_dir(),
            (target_dir / "compressed").is_dir(),
            (target_dir / ".transcript_state").is_file(),
        )
    )
    return {
        "target_dir": str(target_dir),
        "sessions": sessions,
        "ready_sessions": ready,
        "needs_rebuild_sessions": needs_rebuild,
        "legacy_layout": legacy_layout,
        "index_exists": (
            target_dir / "index" / "context.sqlite3"
        ).is_file(),
    }


def legacy_entry_id(raw_line: bytes) -> str:
    try:
        entry = json.loads(raw_line)
    except json.JSONDecodeError:
        return hashlib.sha256(raw_line).hexdigest()
    explicit = entry.get("uuid") or entry.get("id")
    if explicit:
        return str(explicit)
    payload = entry.get("payload") or {}
    stable = "|".join(
        str(value or "")
        for value in (
            entry.get("timestamp"),
            entry.get("type"),
            payload.get("type"),
            payload.get("role"),
            payload.get("turn_id"),
        )
    )
    return stable if stable.strip("|") else hashlib.sha256(raw_line).hexdigest()


def merge_legacy(target_dir: Path) -> dict:
    target_dir = Path(target_dir).expanduser().resolve()
    raw_dir = target_dir / "raw"
    if not raw_dir.is_dir():
        raise ValueError("旧布局 raw/ 不存在")
    seen: set[str] = set()
    merged: list[bytes] = []
    input_files = sorted(raw_dir.glob("*.jsonl"))
    for path in input_files:
        for raw_line in path.read_bytes().splitlines(keepends=True):
            if not raw_line:
                continue
            entry_id = legacy_entry_id(raw_line)
            if entry_id in seen:
                continue
            seen.add(entry_id)
            merged.append(
                raw_line if raw_line.endswith(b"\n") else raw_line + b"\n"
            )
    output = target_dir / "legacy-merged" / "merged.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output.with_suffix(output.suffix + ".tmp")
    with temp_path.open("wb") as handle:
        handle.writelines(merged)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, output)
    return {
        "input_files": len(input_files),
        "unique_entries": len(merged),
        "output": str(output),
    }


def compress_legacy(input_path: Path, output_path: Path | None = None) -> Path:
    input_path = Path(input_path).expanduser().resolve()
    if output_path is None:
        output_path = input_path.with_name(
            f"{input_path.stem}_compressed.md"
        )
    else:
        output_path = Path(output_path).expanduser().resolve()
    lines = [
        "# Legacy Transcript Deterministic View",
        "",
        f"> source: `{input_path}`",
        "",
    ]
    for line_number, raw_line in enumerate(
        input_path.read_bytes().splitlines(),
        start=1,
    ):
        preview = raw_line.decode("utf-8", errors="replace")[:2000]
        lines.extend(
            [
                f"## Entry {line_number}",
                "",
                "```json",
                preview,
                "```",
                "",
            ]
        )
    atomic_write_text(output_path, "\n".join(lines))
    return output_path
