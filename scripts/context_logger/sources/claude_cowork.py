from pathlib import Path

from ..models import SourceSession


def resolve(workspace_root: Path) -> SourceSession:
    return SourceSession(
        source="claude-cowork",
        session_id="",
        session_path=None,
        workspace_root=workspace_root.expanduser().resolve(),
        confidence="unsupported",
        completeness="unsupported",
    )
