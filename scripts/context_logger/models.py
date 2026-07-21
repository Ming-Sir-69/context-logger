from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResolveOptions:
    source: str = "auto"
    session_id: str | None = None
    session_file: Path | None = None
    workspace_root: Path | None = None
    project_root: Path | None = None
    target_dir: Path | None = None
    workspace_manifest: Path | None = None
    module_id: str | None = None


@dataclass(frozen=True)
class SourceSession:
    source: str
    session_id: str
    session_path: Path | None
    workspace_root: Path
    confidence: str
    completeness: str


@dataclass(frozen=True)
class Resolution:
    source: str
    session_id: str
    session_path: Path | None
    workspace_root: Path
    target_dir: Path
    confidence: str
    completeness: str
    target_overridden: bool
    module_id: str | None = None


@dataclass(frozen=True)
class NormalizedEvent:
    schema_version: int
    event_id: str
    source: str
    source_session_id: str
    ordinal: int
    timestamp: str
    category: str
    role: str | None
    text: str
    tool_name: str | None
    tool_call_id: str | None
    turn_id: str | None
    payload_type: str
    raw_ref: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "source": self.source,
            "source_session_id": self.source_session_id,
            "ordinal": self.ordinal,
            "timestamp": self.timestamp,
            "category": self.category,
            "role": self.role,
            "text": self.text,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "turn_id": self.turn_id,
            "payload_type": self.payload_type,
            "raw_ref": self.raw_ref,
        }
