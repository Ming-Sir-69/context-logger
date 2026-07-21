import json
import os
from pathlib import Path

from .models import Resolution, ResolveOptions, SourceSession
from .sources import claude_code, claude_cowork, codex


class ResolutionConflictError(ValueError):
    pass


def canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def find_workspace_manifest(start: Path) -> Path | None:
    current = canonical(start)
    for candidate in (current, *current.parents):
        manifest = candidate / "workspace.json"
        if manifest.is_file():
            return manifest
    return None


def load_workspace_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("workspace.json schema_version 必须为 1")
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise ValueError("workspace.json modules 必须为数组")
    module_ids = [module.get("id") for module in modules]
    module_paths = [module.get("path") for module in modules]
    if any(not isinstance(value, str) or not value for value in module_ids):
        raise ValueError("workspace.json 模块 id 必须为非空字符串")
    if len(set(module_ids)) != len(module_ids):
        raise ValueError("workspace.json 存在重复模块 id")
    if any(not isinstance(value, str) or not value for value in module_paths):
        raise ValueError("workspace.json 模块 path 必须为非空字符串")
    if len(set(module_paths)) != len(module_paths):
        raise ValueError("workspace.json 存在重复模块 path")
    return data


def resolve_source(options: ResolveOptions, base_workspace: Path) -> SourceSession:
    if options.source == "claude-cowork":
        return claude_cowork.resolve(base_workspace)
    if options.session_file is not None:
        path = canonical(options.session_file)
        if options.source == "codex":
            try:
                return codex.resolve(session_file=path)
            except ValueError as error:
                raise ResolutionConflictError(
                    f"显式来源 codex 与 Session 文件冲突: {error}"
                ) from error
        if options.source in ("claude", "claude-code"):
            try:
                return claude_code.resolve(session_file=path)
            except ValueError as error:
                raise ResolutionConflictError(
                    f"显式来源 claude-code 与 Session 文件冲突: {error}"
                ) from error
        try:
            return codex.resolve(session_file=path)
        except ValueError:
            return claude_code.resolve(session_file=path)
    if (
        options.source == "auto"
        and os.environ.get("CONTEXT_LOGGER_CLAUDE_SESSION_ID")
    ):
        return claude_code.resolve(session_id=options.session_id)
    if options.source in ("auto", "codex"):
        return codex.resolve(session_id=options.session_id)
    if options.source in ("claude", "claude-code"):
        return claude_code.resolve(
            session_file=options.session_file,
            session_id=options.session_id,
        )
    raise ValueError(f"不支持的来源: {options.source}")


def resolve_module_target(
    workspace_root: Path,
    manifest_path: Path,
    module_id: str | None,
) -> tuple[Path, str]:
    manifest = load_workspace_manifest(manifest_path)
    modules = manifest["modules"]
    module_ids = [module.get("id", "") for module in modules]
    if not module_id:
        available = ", ".join(module_ids)
        raise ValueError(
            "受管工作区必须指定 --module-id，"
            f"可用模块: {available}"
        )
    matches = [module for module in modules if module.get("id") == module_id]
    if len(matches) != 1:
        raise ValueError(f"未找到唯一模块: {module_id}")
    module = matches[0]
    if module.get("transcripts_path") != "project_context/transcripts":
        raise ValueError(f"模块 transcripts_path 无效: {module_id}")
    module_root = canonical(workspace_root / module.get("path", ""))
    if workspace_root not in module_root.parents:
        raise ValueError(f"模块越出工作区: {module_id}")
    if not module_root.is_dir():
        raise ValueError(f"模块目录不存在: {module_id}")
    target = canonical(module_root / module["transcripts_path"])
    if module_root not in target.parents:
        raise ValueError(f"归档目录越出模块: {module_id}")
    return target, module_id


def registered_target_for_explicit_path(
    workspace_root: Path,
    manifest_path: Path,
    target_dir: Path,
    module_id: str | None,
) -> tuple[Path, str]:
    manifest = load_workspace_manifest(manifest_path)
    if module_id:
        registered, resolved_id = resolve_module_target(
            workspace_root,
            manifest_path,
            module_id,
        )
        if registered != target_dir:
            raise ResolutionConflictError(
                f"显式目标与登记模块不一致: {module_id}"
            )
        return registered, resolved_id
    matches = []
    for module in manifest["modules"]:
        registered, resolved_id = resolve_module_target(
            workspace_root,
            manifest_path,
            module["id"],
        )
        if registered == target_dir:
            matches.append((registered, resolved_id))
    if len(matches) != 1:
        available = ", ".join(
            str(
                canonical(workspace_root / module["path"])
                / module["transcripts_path"]
            )
            for module in manifest["modules"]
        )
        raise ResolutionConflictError(
            "受管工作区的显式目标未命中唯一登记模块；"
            f"可用目标: {available}"
        )
    return matches[0]


def resolve_context(options: ResolveOptions) -> Resolution:
    base_workspace = canonical(
        options.workspace_root
        or options.project_root
        or Path.cwd()
    )
    source_session = resolve_source(options, base_workspace)
    workspace_root = canonical(
        options.workspace_root
        or options.project_root
        or source_session.workspace_root
    )

    target_overridden = False
    resolved_module = options.module_id
    if options.target_dir is not None:
        target_dir = canonical(options.target_dir)
        target_overridden = True
        manifest_path = (
            canonical(options.workspace_manifest)
            if options.workspace_manifest
            else find_workspace_manifest(workspace_root)
        )
        if manifest_path:
            workspace_root = manifest_path.parent
            manifest = load_workspace_manifest(manifest_path)
            if (
                manifest.get("context_policy")
                == "require_registered_module"
                or options.module_id
            ):
                target_dir, resolved_module = (
                    registered_target_for_explicit_path(
                        workspace_root,
                        manifest_path,
                        target_dir,
                        options.module_id,
                    )
                )
    else:
        manifest_path = (
            canonical(options.workspace_manifest)
            if options.workspace_manifest
            else find_workspace_manifest(workspace_root)
        )
        if manifest_path:
            workspace_root = manifest_path.parent
            manifest = load_workspace_manifest(manifest_path)
            if (
                manifest.get("context_policy")
                == "require_registered_module"
                or options.module_id
            ):
                target_dir, resolved_module = resolve_module_target(
                    workspace_root,
                    manifest_path,
                    options.module_id,
                )
            else:
                target_dir = canonical(
                    workspace_root / "project_context" / "transcripts"
                )
        else:
            target_dir = canonical(
                workspace_root / "project_context" / "transcripts"
            )
            target_overridden = options.project_root is not None

    source_workspace = canonical(source_session.workspace_root)
    workspaces_related = (
        source_workspace == workspace_root
        or workspace_root in source_workspace.parents
        or source_workspace in workspace_root.parents
    )
    if not workspaces_related:
        raise ResolutionConflictError(
            "Session 工作区与归档工作区无包含关系: "
            f"session={source_workspace}; target={workspace_root}"
        )

    return Resolution(
        source=source_session.source,
        session_id=source_session.session_id,
        session_path=source_session.session_path,
        workspace_root=workspace_root,
        target_dir=target_dir,
        confidence=source_session.confidence,
        completeness=source_session.completeness,
        target_overridden=target_overridden,
        module_id=resolved_module,
    )
