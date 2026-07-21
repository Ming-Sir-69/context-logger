import hashlib
import json
from pathlib import Path
from typing import Any

from .models import NormalizedEvent


def safe_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(safe_text(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") == "text":
            return safe_text(value.get("text", ""))
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def raw_reference(part_path: Path, line_number: int, raw_line: bytes) -> dict:
    if part_path.parent.name == "raw":
        part = str(Path("raw") / part_path.name)
    else:
        part = str(part_path)
    return {
        "part": part,
        "line": line_number,
        "sha256": hashlib.sha256(raw_line).hexdigest(),
    }


def stable_event_id(
    entry: dict,
    session_id: str,
    ordinal: int,
    raw_hash: str,
    suffix: int = 0,
) -> str:
    payload = entry.get("payload", {})
    explicit = (
        entry.get("uuid")
        or payload.get("id")
        or payload.get("call_id")
        or entry.get("id")
    )
    base = explicit or hashlib.sha256(
        f"{session_id}|{ordinal}|{raw_hash}".encode("utf-8")
    ).hexdigest()
    return str(base) if suffix == 0 else f"{base}:{suffix}"


def make_event(
    *,
    entry: dict,
    source: str,
    session_id: str,
    ordinal: int,
    raw_ref: dict,
    category: str,
    role: str | None,
    text: str,
    payload_type: str,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    turn_id: str | None = None,
    suffix: int = 0,
) -> NormalizedEvent:
    return NormalizedEvent(
        schema_version=1,
        event_id=stable_event_id(
            entry,
            session_id,
            ordinal,
            raw_ref["sha256"],
            suffix,
        ),
        source=source,
        source_session_id=session_id,
        ordinal=ordinal,
        timestamp=entry.get("timestamp", ""),
        category=category,
        role=role,
        text=text,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        turn_id=turn_id,
        payload_type=payload_type,
        raw_ref=raw_ref,
    )


def normalize_codex(
    entry: dict,
    session_id: str,
    ordinal: int,
    raw_ref: dict,
) -> list[NormalizedEvent]:
    entry_type = entry.get("type", "")
    payload = entry.get("payload", {})
    payload_type = payload.get("type", entry_type)

    if entry_type in ("session_meta", "turn_context"):
        return [
            make_event(
                entry=entry,
                source="codex",
                session_id=session_id,
                ordinal=ordinal,
                raw_ref=raw_ref,
                category="metadata",
                role=None,
                text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                payload_type=payload_type,
                turn_id=payload.get("turn_id"),
            )
        ]

    if entry_type == "event_msg":
        if payload_type == "user_message":
            category, role = "message", "user"
        elif payload_type in ("agent_message", "assistant_message"):
            category, role = "message", "assistant"
        elif payload_type in ("agent_reasoning", "reasoning"):
            category, role = "reasoning", "assistant"
        else:
            category, role = "unknown", None
        return [
            make_event(
                entry=entry,
                source="codex",
                session_id=session_id,
                ordinal=ordinal,
                raw_ref=raw_ref,
                category=category,
                role=role,
                text=safe_text(payload.get("text", payload)),
                payload_type=payload_type,
                turn_id=payload.get("turn_id"),
            )
        ]

    if entry_type == "response_item":
        if payload_type == "message":
            texts = []
            for item in payload.get("content", []) or []:
                if item.get("type") in ("input_text", "output_text", "text"):
                    texts.append(item.get("text", ""))
            return [
                make_event(
                    entry=entry,
                    source="codex",
                    session_id=session_id,
                    ordinal=ordinal,
                    raw_ref=raw_ref,
                    category="message",
                    role=payload.get("role"),
                    text="\n".join(texts),
                    payload_type=payload_type,
                    turn_id=payload.get("turn_id"),
                )
            ]
        if payload_type == "function_call":
            return [
                make_event(
                    entry=entry,
                    source="codex",
                    session_id=session_id,
                    ordinal=ordinal,
                    raw_ref=raw_ref,
                    category="tool_call",
                    role="assistant",
                    text=safe_text(payload.get("arguments", "")),
                    payload_type=payload_type,
                    tool_name=payload.get("name"),
                    tool_call_id=payload.get("call_id"),
                    turn_id=payload.get("turn_id"),
                )
            ]
        if payload_type == "function_call_output":
            return [
                make_event(
                    entry=entry,
                    source="codex",
                    session_id=session_id,
                    ordinal=ordinal,
                    raw_ref=raw_ref,
                    category="tool_result",
                    role="tool",
                    text=safe_text(payload.get("output", "")),
                    payload_type=payload_type,
                    tool_call_id=payload.get("call_id"),
                    turn_id=payload.get("turn_id"),
                )
            ]
        if payload_type == "reasoning":
            return [
                make_event(
                    entry=entry,
                    source="codex",
                    session_id=session_id,
                    ordinal=ordinal,
                    raw_ref=raw_ref,
                    category="reasoning",
                    role="assistant",
                    text=safe_text(payload.get("summary", "")),
                    payload_type=payload_type,
                    turn_id=payload.get("turn_id"),
                )
            ]

    return [
        make_event(
            entry=entry,
            source="codex",
            session_id=session_id,
            ordinal=ordinal,
            raw_ref=raw_ref,
            category="unknown",
            role=None,
            text=json.dumps(entry, ensure_ascii=False, sort_keys=True),
            payload_type=payload_type,
        )
    ]


def normalize_claude(
    entry: dict,
    session_id: str,
    ordinal: int,
    raw_ref: dict,
) -> list[NormalizedEvent]:
    entry_type = entry.get("type", "")
    content = entry.get("message", {}).get("content", [])
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    events: list[NormalizedEvent] = []

    if entry_type == "assistant":
        for suffix, item in enumerate(content):
            item_type = item.get("type", "")
            if item_type == "text":
                category, role = "message", "assistant"
                text = item.get("text", "")
                tool_name = tool_call_id = None
            elif item_type == "tool_use":
                category, role = "tool_call", "assistant"
                text = safe_text(item.get("input", {}))
                tool_name = item.get("name")
                tool_call_id = item.get("id")
            elif item_type == "thinking":
                category, role = "reasoning", "assistant"
                text = item.get("thinking", "")
                tool_name = tool_call_id = None
            else:
                category, role = "unknown", None
                text = safe_text(item)
                tool_name = tool_call_id = None
            events.append(
                make_event(
                    entry=entry,
                    source="claude-code",
                    session_id=session_id,
                    ordinal=ordinal,
                    raw_ref=raw_ref,
                    category=category,
                    role=role,
                    text=text,
                    payload_type=item_type or entry_type,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    suffix=suffix,
                )
            )
        return events

    if entry_type == "user":
        for suffix, item in enumerate(content):
            item_type = item.get("type", "")
            if item_type == "tool_result":
                category, role = "tool_result", "tool"
                text = safe_text(item.get("content", ""))
                tool_call_id = item.get("tool_use_id")
            elif item_type == "text":
                category, role = "message", "user"
                text = item.get("text", "")
                tool_call_id = None
            else:
                category, role = "unknown", None
                text = safe_text(item)
                tool_call_id = None
            events.append(
                make_event(
                    entry=entry,
                    source="claude-code",
                    session_id=session_id,
                    ordinal=ordinal,
                    raw_ref=raw_ref,
                    category=category,
                    role=role,
                    text=text,
                    payload_type=item_type or entry_type,
                    tool_call_id=tool_call_id,
                    suffix=suffix,
                )
            )
        return events

    category = "system" if entry_type == "system" else "unknown"
    return [
        make_event(
            entry=entry,
            source="claude-code",
            session_id=session_id,
            ordinal=ordinal,
            raw_ref=raw_ref,
            category=category,
            role="system" if category == "system" else None,
            text=json.dumps(entry, ensure_ascii=False, sort_keys=True),
            payload_type=entry_type,
        )
    ]


def normalize_raw_part(
    source: str,
    session_id: str,
    part_path: Path,
    start_ordinal: int = 1,
) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    with part_path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            ordinal = start_ordinal + line_number - 1
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                entry = {"type": "invalid_json", "raw": raw_line.decode(
                    "utf-8",
                    errors="replace",
                )}
            reference = raw_reference(part_path, line_number, raw_line)
            if source == "codex":
                events.extend(
                    normalize_codex(entry, session_id, ordinal, reference)
                )
            elif source in ("claude", "claude-code"):
                events.extend(
                    normalize_claude(entry, session_id, ordinal, reference)
                )
            else:
                events.append(
                    make_event(
                        entry=entry,
                        source=source,
                        session_id=session_id,
                        ordinal=ordinal,
                        raw_ref=reference,
                        category="unknown",
                        role=None,
                        text=json.dumps(
                            entry,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        payload_type=entry.get("type", ""),
                    )
                )
    return events
