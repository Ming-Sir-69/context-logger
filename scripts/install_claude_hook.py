#!/usr/bin/env python3
import argparse
import json
import os
import shlex
import shutil
import stat
import sys
from pathlib import Path


HOOK_MARKER = "context-logger-session-anchor"
SESSION_MATCHER = "startup|resume|clear|compact"


def atomic_write_text(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None and path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        temp_path.chmod(mode)
    os.replace(temp_path, path)


def load_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Claude settings.json 顶层必须是对象")
    hooks = value.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ValueError("Claude settings.json hooks 必须是对象")
    return value


def restore_missing_hooks(
    settings: dict,
    *,
    settings_existed: bool,
    restore_hooks_from: Path | None,
) -> dict:
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        return settings
    if not settings_existed and "hooks" not in settings:
        restored = dict(settings)
        restored["hooks"] = {}
        return restored
    if restore_hooks_from is None:
        raise ValueError(
            "Claude settings.json hooks 缺失或为 null；"
            "请使用 --restore-hooks-from 显式提供可信 Hook 基线"
        )
    baseline_path = restore_hooks_from.expanduser().resolve()
    if not baseline_path.is_file():
        raise ValueError(f"Hook 基线不存在: {baseline_path}")
    with baseline_path.open("r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    if not isinstance(baseline, dict):
        raise ValueError("Hook 基线顶层必须是对象")
    baseline_hooks = baseline.get("hooks")
    if not isinstance(baseline_hooks, dict) or not baseline_hooks:
        raise ValueError("Hook 基线必须包含非空 hooks 对象")
    restored = dict(settings)
    restored["hooks"] = baseline_hooks
    return restored


def write_hook_script(
    hook_script: Path,
    cli_script: Path,
    python_executable: Path,
) -> None:
    content = (
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"exec {shlex.quote(str(python_executable))} "
        f"{shlex.quote(str(cli_script))} anchor\n"
    )
    atomic_write_text(hook_script, content, mode=0o755)


def is_context_logger_handler(handler: object) -> bool:
    return (
        isinstance(handler, dict)
        and HOOK_MARKER in str(handler.get("command") or "")
    )


def merge_session_start_hook(
    settings: dict,
    hook_script: Path,
) -> dict:
    hooks = dict(settings.get("hooks") or {})
    current_groups = hooks.get("SessionStart") or []
    if not isinstance(current_groups, list):
        raise ValueError("hooks.SessionStart 必须是数组")
    preserved_groups = []
    for group in current_groups:
        if not isinstance(group, dict):
            preserved_groups.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            preserved_groups.append(group)
            continue
        preserved_handlers = [
            handler
            for handler in handlers
            if not is_context_logger_handler(handler)
        ]
        if preserved_handlers:
            preserved = dict(group)
            preserved["hooks"] = preserved_handlers
            preserved_groups.append(preserved)
    preserved_groups.append(
        {
            "matcher": SESSION_MATCHER,
            "hooks": [
                {
                    "type": "command",
                    "command": (
                        f"{shlex.quote(str(hook_script))} # {HOOK_MARKER}"
                    ),
                    "timeout": 5,
                }
            ],
        }
    )
    hooks["SessionStart"] = preserved_groups
    merged = dict(settings)
    merged["hooks"] = hooks
    return merged


def install(
    settings_path: Path,
    cli_script: Path,
    hook_script: Path,
    restore_hooks_from: Path | None = None,
) -> tuple[Path, Path]:
    settings_path = settings_path.expanduser().resolve()
    cli_script = cli_script.expanduser().resolve()
    hook_script = hook_script.expanduser().resolve()
    if not cli_script.is_file():
        raise ValueError(f"Context Logger CLI 不存在: {cli_script}")

    settings_existed = settings_path.is_file()
    settings = load_settings(settings_path)
    settings = restore_missing_hooks(
        settings,
        settings_existed=settings_existed,
        restore_hooks_from=restore_hooks_from,
    )
    backup_path = settings_path.with_suffix(
        settings_path.suffix + ".context-logger.bak"
    )
    if not backup_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if settings_path.is_file():
            shutil.copy2(settings_path, backup_path)
        else:
            atomic_write_text(backup_path, "{}\n")

    write_hook_script(
        hook_script,
        cli_script,
        Path(sys.executable).resolve(),
    )
    merged = merge_session_start_hook(settings, hook_script)
    atomic_write_text(
        settings_path,
        json.dumps(
            merged,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return backup_path, hook_script


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="保留式安装 Context Logger Claude Code Session 锚点"
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path.home() / ".claude" / "settings.json",
    )
    parser.add_argument(
        "--script",
        type=Path,
        required=True,
        help="已部署的 transcript_manager.py",
    )
    parser.add_argument(
        "--hook-script",
        type=Path,
        default=(
            Path.home()
            / ".claude"
            / "hooks"
            / "context-logger-session-anchor.sh"
        ),
    )
    parser.add_argument(
        "--restore-hooks-from",
        type=Path,
        help=(
            "仅当现有 hooks 缺失或为 null 时，"
            "从该 JSON 的非空 hooks 对象恢复基线"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        backup, hook_script = install(
            args.settings,
            args.script,
            args.hook_script,
            args.restore_hooks_from,
        )
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    print(f"settings={args.settings.expanduser().resolve()}")
    print(f"backup={backup}")
    print(f"hook_script={hook_script}")
    print("hook_event=SessionStart")
    print("idempotent=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
