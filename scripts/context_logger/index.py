import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .markdown import canonical_chunk_paths


TOOL_INPUT_INDEX_CHARS = 2000
TOOL_RESULT_INDEX_CHARS = 8000
OTHER_EVENT_INDEX_CHARS = 8000


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    completeness TEXT NOT NULL,
    confidence TEXT NOT NULL,
    session_path TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    module_id TEXT
);

CREATE TABLE raw_entries (
    session_id TEXT NOT NULL,
    part TEXT NOT NULL,
    line INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    source_path TEXT,
    source_start INTEGER,
    source_end INTEGER,
    parse_status TEXT,
    PRIMARY KEY (session_id, part, line),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE chunks (
    session_id TEXT NOT NULL,
    chunk_path TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    first_ordinal INTEGER,
    last_ordinal INTEGER,
    first_timestamp TEXT,
    last_timestamp TEXT,
    PRIMARY KEY (session_id, chunk_path),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE events (
    row_id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    role TEXT,
    text TEXT NOT NULL,
    tool_name TEXT,
    tool_call_id TEXT,
    turn_id TEXT,
    payload_type TEXT NOT NULL,
    chunk_path TEXT,
    raw_ref TEXT NOT NULL,
    UNIQUE (session_id, event_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE archive_state (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE VIRTUAL TABLE search_fts USING fts5(
    event_id UNINDEXED,
    session_id UNINDEXED,
    source UNINDEXED,
    role UNINDEXED,
    category UNINDEXED,
    content,
    tokenize = 'unicode61'
);
"""


@dataclass(frozen=True)
class SearchHit:
    event_id: str
    session_id: str
    source: str
    role: str | None
    category: str
    chunk_path: str
    snippet: str
    raw_ref: dict[str, Any]
    score: float


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Normalized JSONL 损坏: {path}#{line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError(
                    f"Normalized 事件必须是对象: {path}#{line_number}"
                )
            yield value


def event_index_content(event: dict) -> str:
    text = str(event.get("text") or "")
    category = str(event.get("category") or "unknown")
    if category == "message":
        return text
    if category == "tool_call":
        tool_name = str(event.get("tool_name") or "")
        return f"{tool_name}\n{text[:TOOL_INPUT_INDEX_CHARS]}".strip()
    if category == "tool_result":
        return text[:TOOL_RESULT_INDEX_CHARS]
    return text[:OTHER_EVENT_INDEX_CHARS]


def parse_header_value(text: str, name: str) -> str:
    match = re.search(
        rf"^> {re.escape(name)}: `([^`]*)`",
        text,
        re.MULTILINE,
    )
    return match.group(1) if match else ""


def parse_range(value: str) -> tuple[int | None, int | None]:
    match = re.fullmatch(r"(\d+)-(\d+)", value)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def chunk_records(session_dir: Path) -> tuple[list[dict], dict[str, str]]:
    records = []
    event_chunks: dict[str, str] = {}
    for path in canonical_chunk_paths(session_dir / "context"):
        content = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(session_dir))
        first_ordinal, last_ordinal = parse_range(
            parse_header_value(content, "event_range")
        )
        time_range = parse_header_value(content, "time_range")
        if " — " in time_range:
            first_timestamp, last_timestamp = time_range.split(" — ", 1)
        else:
            first_timestamp = last_timestamp = ""
        records.append(
            {
                "chunk_path": relative,
                "char_count": len(content),
                "sha256": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                "first_ordinal": first_ordinal,
                "last_ordinal": last_ordinal,
                "first_timestamp": first_timestamp,
                "last_timestamp": last_timestamp,
            }
        )
        for event_id in re.findall(
            r"^> event_id: `([^`]+)`",
            content,
            re.MULTILINE,
        ):
            event_chunks.setdefault(event_id, relative)
    return records, event_chunks


def scan_raw_entries(session_dir: Path):
    raw_dir = session_dir / "raw"
    if not raw_dir.is_dir():
        return
    for part in sorted(raw_dir.glob("part-*.jsonl")):
        relative = str(part.relative_to(session_dir))
        for line_number, raw_line in enumerate(
            part.read_bytes().splitlines(keepends=True),
            start=1,
        ):
            if not raw_line:
                continue
            yield {
                "part": relative,
                "line": line_number,
                "sha256": hashlib.sha256(raw_line).hexdigest(),
            }


def quote_fts_query(query: str) -> str:
    query = query.strip()
    if not query:
        raise ValueError("检索词不能为空")
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms:
        raise ValueError("检索词不包含可索引文字")
    return " AND ".join(
        '"' + term.replace('"', '""') + '"'
        for term in terms
    )


class ContextIndex:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser().resolve()

    def rebuild(
        self,
        target_dir: Path,
        state_overrides: dict[str, dict] | None = None,
    ) -> None:
        target_dir = Path(target_dir).expanduser().resolve()
        sessions_dir = target_dir / "sessions"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.db_path.name}.",
            suffix=".rebuild",
            dir=self.db_path.parent,
        )
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            with closing(sqlite3.connect(temp_path)) as connection:
                with connection:
                    connection.executescript(SCHEMA)
                    if sessions_dir.is_dir():
                        for session_dir in sorted(
                            path
                            for path in sessions_dir.iterdir()
                            if path.is_dir()
                        ):
                            self._index_session(
                                connection,
                                session_dir,
                                (state_overrides or {}).get(
                                    self._manifest_session_id(session_dir)
                                ),
                            )
                    connection.commit()
            with temp_path.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp_path, self.db_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _index_session(
        self,
        connection: sqlite3.Connection,
        session_dir: Path,
        state_override: dict | None = None,
    ) -> None:
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"Session 缺少 manifest.json: {session_dir}")
        manifest = read_json(manifest_path)
        session_id = str(manifest.get("session_id") or "")
        source = str(manifest.get("source") or "")
        if not session_id or not source:
            raise ValueError(f"Session Manifest 身份不完整: {manifest_path}")

        connection.execute(
            """
            INSERT INTO sessions (
                session_id, source, completeness, confidence,
                session_path, workspace_root, module_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                source,
                str(manifest.get("completeness") or "unsupported"),
                str(manifest.get("confidence") or "unsupported"),
                str(manifest.get("session_path") or ""),
                str(manifest.get("workspace_root") or ""),
                manifest.get("module_id"),
            ),
        )

        raw_entries = (
            manifest.get("raw_entries")
            or manifest.get("raw_records")
            or list(scan_raw_entries(session_dir) or ())
        )
        for entry in raw_entries:
            connection.execute(
                """
                INSERT INTO raw_entries (
                    session_id, part, line, sha256, source_path,
                    source_start, source_end, parse_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(entry.get("part") or ""),
                    int(entry.get("line") or 0),
                    str(entry.get("sha256") or ""),
                    entry.get("source_path"),
                    entry.get("source_start"),
                    entry.get("source_end"),
                    entry.get("parse_status"),
                ),
            )

        chunks, event_chunks = chunk_records(session_dir)
        for chunk in chunks:
            connection.execute(
                """
                INSERT INTO chunks (
                    session_id, chunk_path, char_count, sha256,
                    first_ordinal, last_ordinal,
                    first_timestamp, last_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    chunk["chunk_path"],
                    chunk["char_count"],
                    chunk["sha256"],
                    chunk["first_ordinal"],
                    chunk["last_ordinal"],
                    chunk["first_timestamp"],
                    chunk["last_timestamp"],
                ),
            )

        normalized_paths = sorted(
            (session_dir / "normalized").glob("events-*.jsonl")
        )
        if not normalized_paths:
            raise ValueError(f"Session 缺少 Normalized 事件: {session_dir}")
        for normalized_path in normalized_paths:
            for event in read_jsonl(normalized_path):
                event_id = str(event.get("event_id") or "")
                if not event_id:
                    raise ValueError(
                        f"Normalized 事件缺少 event_id: {normalized_path}"
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO events (
                        event_id, session_id, ordinal, timestamp,
                        category, role, text, tool_name, tool_call_id,
                        turn_id, payload_type, chunk_path, raw_ref
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        session_id,
                        int(event.get("ordinal") or 0),
                        str(event.get("timestamp") or ""),
                        str(event.get("category") or "unknown"),
                        event.get("role"),
                        str(event.get("text") or ""),
                        event.get("tool_name"),
                        event.get("tool_call_id"),
                        event.get("turn_id"),
                        str(event.get("payload_type") or ""),
                        event_chunks.get(event_id, ""),
                        json.dumps(
                            event.get("raw_ref") or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                )
                row_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO search_fts (
                        rowid, event_id, session_id, source,
                        role, category, content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        event_id,
                        session_id,
                        source,
                        event.get("role"),
                        str(event.get("category") or "unknown"),
                        event_index_content(event),
                    ),
                )

        state_path = session_dir / "state.json"
        state = (
            state_override
            if state_override is not None
            else read_json(state_path) if state_path.is_file() else {}
        )
        connection.execute(
            "INSERT INTO archive_state (session_id, state_json) VALUES (?, ?)",
            (
                session_id,
                json.dumps(state, ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _manifest_session_id(session_dir: Path) -> str:
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.is_file():
            return ""
        return str(read_json(manifest_path).get("session_id") or "")

    def search(
        self,
        query: str,
        filters: dict[str, str] | None = None,
        *,
        session_id: str | None = None,
        source: str | None = None,
        role: str | None = None,
        category: str | None = None,
        limit: int = 10,
        budget_chars: int = 12000,
    ) -> list[SearchHit]:
        if not self.db_path.is_file():
            raise ValueError(f"索引不存在: {self.db_path}")
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        if budget_chars <= 0:
            raise ValueError("budget_chars 必须大于 0")
        merged = dict(filters or {})
        session_id = session_id or merged.get("session_id")
        source = source or merged.get("source")
        role = role or merged.get("role")
        category = category or merged.get("category")

        where = ["search_fts MATCH ?"]
        parameters: list[Any] = [quote_fts_query(query)]
        for column, value in (
            ("e.session_id", session_id),
            ("s.source", source),
            ("e.role", role),
            ("e.category", category),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                parameters.append(value)
        parameters.append(limit)
        statement = f"""
            SELECT
                e.event_id,
                e.session_id,
                s.source,
                e.role,
                e.category,
                e.chunk_path,
                snippet(search_fts, 5, '[', ']', '…', 32),
                e.raw_ref,
                bm25(search_fts)
            FROM search_fts
            JOIN events AS e ON e.row_id = search_fts.rowid
            JOIN sessions AS s ON s.session_id = e.session_id
            WHERE {' AND '.join(where)}
            ORDER BY bm25(search_fts), e.ordinal, e.event_id
            LIMIT ?
        """
        hits: list[SearchHit] = []
        used = 0
        with closing(sqlite3.connect(self.db_path)) as connection:
            for row in connection.execute(statement, parameters):
                snippet = str(row[6] or "")
                remaining = budget_chars - used
                if remaining <= 0:
                    break
                if len(snippet) > remaining:
                    snippet = snippet[:remaining]
                hits.append(
                    SearchHit(
                        event_id=row[0],
                        session_id=row[1],
                        source=row[2],
                        role=row[3],
                        category=row[4],
                        chunk_path=row[5] or "",
                        snippet=snippet,
                        raw_ref=json.loads(row[7]),
                        score=float(row[8]),
                    )
                )
                used += len(snippet)
        return hits
