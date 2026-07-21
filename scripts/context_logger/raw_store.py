import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RawArchiveResult:
    part_path: Path | None
    new_entries: int
    records: list[dict]
    next_offset: int
    state: dict


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def complete_prefix(data: bytes) -> bytes:
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return b""
    return data[: last_newline + 1]


def existing_hash_counts(raw_dir: Path) -> Counter:
    hashes: Counter = Counter()
    if not raw_dir.is_dir():
        return hashes
    for part in sorted(raw_dir.glob("part-*.jsonl")):
        for line in part.read_bytes().splitlines(keepends=True):
            if line:
                hashes[sha256_bytes(line)] += 1
    return hashes


def next_part_path(raw_dir: Path) -> Path:
    numbers = []
    for path in raw_dir.glob("part-*.jsonl"):
        try:
            numbers.append(int(path.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return raw_dir / f"part-{max(numbers, default=0) + 1:06d}.jsonl"


def archive_raw(
    source_path: Path,
    session_dir: Path,
    state: dict,
) -> RawArchiveResult:
    source_path = source_path.expanduser().resolve()
    session_dir = session_dir.expanduser().resolve()
    raw_dir = session_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    snapshot = source_path.read_bytes()
    previous_path = state.get("source_path")
    previous_offset = int(state.get("next_offset", 0))
    previous_prefix_hash = state.get("source_prefix_sha256", "")
    cursor_valid = (
        previous_path == str(source_path)
        and 0 <= previous_offset <= len(snapshot)
        and sha256_bytes(snapshot[:previous_offset]) == previous_prefix_hash
    )

    start_offset = previous_offset if cursor_valid else 0
    complete = complete_prefix(snapshot[start_offset:])
    next_offset = start_offset + len(complete)
    archived_counts = existing_hash_counts(raw_dir)

    new_lines: list[bytes] = []
    records: list[dict] = []
    source_position = start_offset
    for line in complete.splitlines(keepends=True):
        line_hash = sha256_bytes(line)
        line_start = source_position
        line_end = line_start + len(line)
        source_position = line_end
        if not cursor_valid and archived_counts[line_hash] > 0:
            archived_counts[line_hash] -= 1
            continue
        new_lines.append(line)
        try:
            json.loads(line)
            parse_status = "valid"
        except json.JSONDecodeError:
            parse_status = "invalid"
        records.append(
            {
                "sha256": line_hash,
                "source_path": str(source_path),
                "source_start": line_start,
                "source_end": line_end,
                "parse_status": parse_status,
            }
        )

    part_path = None
    if new_lines:
        part_path = next_part_path(raw_dir)
        temp_path = part_path.with_suffix(part_path.suffix + ".tmp")
        with temp_path.open("wb") as handle:
            for line in new_lines:
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, part_path)
        for line_number, record in enumerate(records, start=1):
            record["part"] = str(part_path.relative_to(session_dir))
            record["line"] = line_number

    new_state = {
        "source_path": str(source_path),
        "source_size": len(snapshot),
        "next_offset": next_offset,
        "source_prefix_sha256": sha256_bytes(snapshot[:next_offset]),
    }
    return RawArchiveResult(
        part_path=part_path,
        new_entries=len(new_lines),
        records=records,
        next_offset=next_offset,
        state=new_state,
    )
