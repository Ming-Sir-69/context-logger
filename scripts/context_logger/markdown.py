import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .models import NormalizedEvent, Resolution


TARGET_CHARS = 8000
MAX_CHARS = 12000
TEXT_SEGMENT_CHARS = 7000
TOOL_PREVIEW_CHARS = 2000
INDEX_EXCERPT_CHARS = 300
DECISION_KEYWORDS = (
    "决定",
    "决策",
    "方案",
    "架构",
    "边界",
    "验收",
    "问题",
    "错误",
    "下一步",
)
CANONICAL_CHUNK_NAME = re.compile(r"^chunk-\d{6}\.md$")


@dataclass(frozen=True)
class RenderedBlock:
    event: NormalizedEvent
    text: str


@dataclass(frozen=True)
class ChunkMetadata:
    path: Path
    char_count: int
    first_ordinal: int
    last_ordinal: int
    first_timestamp: str
    last_timestamp: str


def canonical_chunk_paths(context_dir: Path) -> list[Path]:
    """Return only Context Logger's deterministic Markdown chunk files."""
    if not context_dir.is_dir():
        return []
    return sorted(
        path
        for path in context_dir.iterdir()
        if path.is_file() and CANONICAL_CHUNK_NAME.fullmatch(path.name)
    )


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def split_text(text: str, size: int = TEXT_SEGMENT_CHARS) -> list[str]:
    if not text:
        return [""]
    return [text[index:index + size] for index in range(0, len(text), size)]


def raw_ref_text(event: NormalizedEvent) -> str:
    reference = event.raw_ref
    return (
        f"{reference.get('part', '?')}#L{reference.get('line', '?')} "
        f"sha256:{reference.get('sha256', '')}"
    )


def normalized_ref_text(event: NormalizedEvent) -> str:
    return (
        "normalized/events-000001.jsonl"
        f"#event_id={event.event_id}"
    )


def message_segment(
    event: NormalizedEvent,
    segment: str,
    part: int,
    parts: int,
) -> str:
    metadata = json.dumps(
        {
            "event_id": event.event_id,
            "part": part,
            "parts": parts,
            "chars": len(segment),
            "sha256": hashlib.sha256(
                segment.encode("utf-8")
            ).hexdigest(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"<!-- context-logger-message {metadata} -->\n"
        f"{segment}\n"
        "<!-- context-logger-message-end -->"
    )


def reconstruct_message_text(
    chunk_paths: list[Path],
    event_id: str,
) -> str:
    """Reconstruct one full message from deterministic Markdown markers."""
    segments: list[tuple[int, str]] = []
    marker = re.compile(
        r"^<!-- context-logger-message (.+) -->\n",
        re.MULTILINE,
    )
    end_marker = "\n<!-- context-logger-message-end -->"
    for path in chunk_paths:
        content = path.read_text(encoding="utf-8")
        for match in marker.finditer(content):
            metadata = json.loads(match.group(1))
            char_count = int(metadata["chars"])
            start = match.end()
            segment = content[start:start + char_count]
            if content[start + char_count:].startswith(end_marker) is False:
                raise ValueError(f"消息片段结束标记损坏: {path}")
            digest = hashlib.sha256(segment.encode("utf-8")).hexdigest()
            if digest != metadata["sha256"]:
                raise ValueError(f"消息片段哈希不匹配: {path}")
            if metadata["event_id"] == event_id:
                segments.append((int(metadata["part"]), segment))
    segments.sort(key=lambda item: item[0])
    return "".join(segment for _, segment in segments)


def render_event_blocks(event: NormalizedEvent) -> list[RenderedBlock]:
    if event.category == "message" and event.role in ("user", "assistant"):
        segments = split_text(event.text)
        label = "用户" if event.role == "user" else "AI"
        blocks = []
        for index, segment in enumerate(segments, start=1):
            continuation = (
                f"（片段 {index}/{len(segments)}）"
                if len(segments) > 1
                else ""
            )
            blocks.append(
                RenderedBlock(
                    event=event,
                    text=(
                        f"## {label} · 事件 {event.ordinal}{continuation}\n\n"
                        f"> event_id: `{event.event_id}`  \n"
                        f"> raw: `{raw_ref_text(event)}`  \n"
                        f"> normalized: `{normalized_ref_text(event)}`\n\n"
                        f"{message_segment(event, segment, index, len(segments))}"
                        "\n\n"
                    ),
                )
            )
        return blocks

    if event.category == "tool_call":
        preview = event.text[:TOOL_PREVIEW_CHARS]
        note = ""
        if len(event.text) > TOOL_PREVIEW_CHARS:
            note = (
                f"\n工具输入仅显示前 {TOOL_PREVIEW_CHARS} 字符；"
                "完整内容按 Raw 引用定位。\n"
            )
        return [
            RenderedBlock(
                event=event,
                text=(
                    f"## 工具调用 · {event.tool_name or 'unknown'}\n\n"
                    f"> event_id: `{event.event_id}`  \n"
                    f"> raw: `{raw_ref_text(event)}`  \n"
                    f"> normalized: `{normalized_ref_text(event)}`\n\n"
                    f"```text\n{preview}\n```\n"
                    f"{note}\n"
                ),
            )
        ]

    if event.category == "tool_result":
        preview = event.text[:TOOL_PREVIEW_CHARS]
        note = ""
        if len(event.text) > TOOL_PREVIEW_CHARS:
            note = (
                f"\n工具结果仅显示前 {TOOL_PREVIEW_CHARS} 字符；"
                "完整内容按 Raw 引用定位。\n"
            )
        return [
            RenderedBlock(
                event=event,
                text=(
                    "## 工具结果\n\n"
                    f"> event_id: `{event.event_id}`  \n"
                    f"> raw: `{raw_ref_text(event)}`  \n"
                    f"> normalized: `{normalized_ref_text(event)}`\n\n"
                    f"```text\n{preview}\n```\n"
                    f"{note}\n"
                ),
            )
        ]

    preview = event.text[:TOOL_PREVIEW_CHARS]
    note = ""
    if len(event.text) > TOOL_PREVIEW_CHARS:
        note = (
            f"\n该事件仅显示前 {TOOL_PREVIEW_CHARS} 字符；"
            "完整内容按 Raw 引用定位。\n"
        )
    return [
        RenderedBlock(
            event=event,
            text=(
                f"## {event.category} · 事件 {event.ordinal}\n\n"
                f"> event_id: `{event.event_id}`  \n"
                f"> raw: `{raw_ref_text(event)}`  \n"
                f"> normalized: `{normalized_ref_text(event)}`\n\n"
                f"{preview}\n"
                f"{note}\n"
            ),
        )
    ]


def chunk_header(
    resolution: Resolution,
    chunk_number: int,
    blocks: list[RenderedBlock],
) -> str:
    ordinals = [block.event.ordinal for block in blocks]
    timestamps = [
        block.event.timestamp
        for block in blocks
        if block.event.timestamp
    ]
    event_range = (
        f"{min(ordinals)}-{max(ordinals)}"
        if ordinals
        else "(无)"
    )
    time_range = (
        f"{timestamps[0]} — {timestamps[-1]}"
        if timestamps
        else "(无)"
    )
    return (
        f"# Session Context Chunk {chunk_number:06d}\n\n"
        f"> source: `{resolution.source}`  \n"
        f"> session_id: `{resolution.session_id}`  \n"
        f"> completeness: `{resolution.completeness}`  \n"
        f"> module_id: `{resolution.module_id or ''}`  \n"
        f"> time_range: `{time_range}`  \n"
        f"> event_range: `{event_range}`\n\n"
    )


def render_chunk(
    resolution: Resolution,
    chunk_number: int,
    blocks: list[RenderedBlock],
) -> str:
    return chunk_header(resolution, chunk_number, blocks) + "".join(
        block.text for block in blocks
    )


def render_session(
    events: list[NormalizedEvent],
    session_dir: Path,
    resolution: Resolution,
) -> list[Path]:
    context_dir = session_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    for existing in canonical_chunk_paths(context_dir):
        existing.unlink()

    blocks: list[RenderedBlock] = [
        block
        for event in events
        for block in render_event_blocks(event)
    ]
    chunk_blocks: list[list[RenderedBlock]] = []
    current: list[RenderedBlock] = []

    for block in blocks:
        candidate = [*current, block]
        candidate_number = len(chunk_blocks) + 1
        if (
            current
            and len(render_chunk(
                resolution,
                candidate_number,
                candidate,
            )) > TARGET_CHARS
        ):
            chunk_blocks.append(current)
            current = []
            candidate = [block]
            candidate_number += 1
        if len(render_chunk(
            resolution,
            candidate_number,
            candidate,
        )) > MAX_CHARS:
            raise ValueError(
                f"单个 Markdown Block 超过 {MAX_CHARS} 字符: "
                f"{len(render_chunk(resolution, candidate_number, candidate))}"
            )
        current.append(block)

    if current or not chunk_blocks:
        chunk_blocks.append(current)

    paths: list[Path] = []
    chunk_metadata: list[ChunkMetadata] = []
    for index, grouped_blocks in enumerate(chunk_blocks, start=1):
        content = render_chunk(resolution, index, grouped_blocks)
        if len(content) > MAX_CHARS:
            raise ValueError(f"Chunk {index} 超过 {MAX_CHARS} 字符")
        path = context_dir / f"chunk-{index:06d}.md"
        atomic_write_text(path, content)
        paths.append(path)
        block_events = [block.event for block in grouped_blocks]
        ordinals = [event.ordinal for event in block_events]
        timestamps = [
            event.timestamp
            for event in block_events
            if event.timestamp
        ]
        chunk_metadata.append(
            ChunkMetadata(
                path=path,
                char_count=len(content),
                first_ordinal=min(ordinals) if ordinals else 0,
                last_ordinal=max(ordinals) if ordinals else 0,
                first_timestamp=timestamps[0] if timestamps else "",
                last_timestamp=timestamps[-1] if timestamps else "",
            )
        )

    first_user = next(
        (
            event.text[:INDEX_EXCERPT_CHARS]
            for event in events
            if event.category == "message" and event.role == "user"
        ),
        "",
    )
    last_assistant = next(
        (
            event.text[:INDEX_EXCERPT_CHARS]
            for event in reversed(events)
            if event.category == "message" and event.role == "assistant"
        ),
        "",
    )
    tools = sorted(
        {
            event.tool_name
            for event in events
            if event.tool_name
        }
    )
    decision_chunks = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if any(keyword in text for keyword in DECISION_KEYWORDS):
            decision_chunks.append(str(path.relative_to(session_dir)))
    session_timestamps = [
        event.timestamp for event in events if event.timestamp
    ]
    session_time_range = (
        f"{session_timestamps[0]} — {session_timestamps[-1]}"
        if session_timestamps
        else "(无)"
    )

    index_lines = [
        "# Session Index",
        "",
        f"- source: `{resolution.source}`",
        f"- session_id: `{resolution.session_id}`",
        f"- completeness: `{resolution.completeness}`",
        f"- confidence: `{resolution.confidence}`",
        f"- module_id: `{resolution.module_id or ''}`",
        f"- time_range: `{session_time_range}`",
        f"- chunks: {len(paths)}",
        f"- events: {len(events)}",
        f"- derived_health: `ready`",
        "",
        "## 首个用户请求摘录",
        "",
        first_user or "(无)",
        "",
        "## 最后 AI 输出摘录",
        "",
        last_assistant or "(无)",
        "",
        "## 工具",
        "",
        ", ".join(tools) if tools else "(无)",
        "",
        "## Chunk",
        "",
    ]
    for metadata in chunk_metadata:
        relative = metadata.path.relative_to(session_dir)
        time_range = (
            f"{metadata.first_timestamp} — {metadata.last_timestamp}"
            if metadata.first_timestamp
            else "(无)"
        )
        index_lines.append(
            f"- `{relative}` · {metadata.char_count} 字符 · "
            f"事件 {metadata.first_ordinal}-{metadata.last_ordinal} · "
            f"时间 {time_range}"
        )
    index_lines.extend(
        [
            "",
            "## 决策与问题关键词命中",
            "",
            *(
                f"- `{path}`"
                for path in decision_chunks
            ),
        ]
    )
    if not decision_chunks:
        index_lines.append("(无)")
    index_lines.append("")
    atomic_write_text(session_dir / "INDEX.md", "\n".join(index_lines))
    return paths


def render_module_index(target_dir: Path) -> Path:
    sessions_dir = target_dir / "sessions"
    lines = ["# Module Session Index", ""]
    if sessions_dir.is_dir():
        for session_dir in sorted(
            path for path in sessions_dir.iterdir() if path.is_dir()
        ):
            session_index = session_dir / "INDEX.md"
            if not session_index.is_file():
                continue
            text = session_index.read_text(encoding="utf-8")
            session_id = ""
            source = ""
            for line in text.splitlines():
                if line.startswith("- session_id:"):
                    session_id = line.split("`")[1]
                elif line.startswith("- source:"):
                    source = line.split("`")[1]
            lines.append(
                f"- `{session_id or session_dir.name}` · "
                f"source `{source or 'unknown'}` · "
                f"`sessions/{session_dir.name}/INDEX.md`"
            )
    if len(lines) == 2:
        lines.append("(无 Session)")
    lines.append("")
    path = target_dir / "INDEX.md"
    atomic_write_text(path, "\n".join(lines))
    return path
