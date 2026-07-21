import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .archive import (
    DerivedLayerError,
    RawWriteError,
    UnsupportedSourceError,
    archive_status,
    compress_legacy,
    find_session,
    merge_legacy,
    rebuild_archive,
    save_context,
    session_dirs,
    verify_archive,
)
from .index import ContextIndex
from .models import ResolveOptions, Resolution
from .resolution import ResolutionConflictError, resolve_context
from .sources.claude_code import write_anchor


EXIT_OK = 0
EXIT_ARGUMENT = 2
EXIT_UNSUPPORTED = 3
EXIT_CONFLICT = 4
EXIT_RAW = 5
EXIT_DERIVED = 6
EXIT_VERIFY = 7


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def print_resolution(resolution: Resolution, file=None) -> None:
    destination = file or sys.stdout
    values = (
        ("source", resolution.source),
        ("session_id", resolution.session_id),
        (
            "session_path",
            str(resolution.session_path)
            if resolution.session_path is not None
            else "",
        ),
        ("workspace_root", str(resolution.workspace_root)),
        ("target_dir", str(resolution.target_dir)),
        ("confidence", resolution.confidence),
        ("completeness", resolution.completeness),
        ("target_overridden", bool_text(resolution.target_overridden)),
        ("module_id", resolution.module_id or ""),
    )
    for key, value in values:
        print(f"{key}={value}", file=destination)


def add_resolution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        default="auto",
        choices=(
            "auto",
            "codex",
            "claude",
            "claude-code",
            "claude-cowork",
        ),
    )
    parser.add_argument("--session-id")
    parser.add_argument("--session-file", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--target-dir", type=Path)
    parser.add_argument("--workspace-manifest", type=Path)
    parser.add_argument("--module-id")


def options_from(args: argparse.Namespace) -> ResolveOptions:
    return ResolveOptions(
        source=args.source,
        session_id=args.session_id,
        session_file=args.session_file,
        workspace_root=args.workspace_root,
        project_root=args.project_root,
        target_dir=args.target_dir,
        workspace_manifest=args.workspace_manifest,
        module_id=args.module_id,
    )


def target_from(args: argparse.Namespace) -> Path:
    target = getattr(args, "target_dir", None)
    if target is None:
        raise ValueError("该命令需要 --target-dir")
    return target.expanduser().resolve()


def command_resolve(args: argparse.Namespace) -> int:
    print_resolution(resolve_context(options_from(args)))
    return EXIT_OK


def command_anchor(args: argparse.Namespace) -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("SessionStart Hook 输入必须是 JSON 对象")
    write_anchor(payload, args.anchors_dir)
    return EXIT_OK


def command_save(args: argparse.Namespace) -> int:
    resolution = resolve_context(options_from(args))
    if resolution.completeness != "complete":
        print_resolution(resolution, file=sys.stderr)
        return EXIT_UNSUPPORTED
    result = save_context(resolution)
    print_resolution(resolution)
    print(f"session_dir={result.session_dir}")
    print(f"new_raw_entries={result.new_raw_entries}")
    print(f"total_raw_entries={result.total_raw_entries}")
    print(f"normalized_events={result.normalized_events}")
    print(f"chunks={result.chunks}")
    print(f"derived_health={result.derived_health}")
    return EXIT_OK


def command_status(args: argparse.Namespace) -> int:
    status = archive_status(target_from(args))
    for key, value in status.items():
        if isinstance(value, bool):
            value = bool_text(value)
        print(f"{key}={value}")
    return EXIT_OK


def command_search(args: argparse.Namespace) -> int:
    target = target_from(args)
    hits = ContextIndex(
        target / "index" / "context.sqlite3"
    ).search(
        args.query,
        session_id=args.session_id,
        source=args.source,
        role=args.role,
        category=args.category,
        limit=args.limit,
        budget_chars=args.budget_chars,
    )
    print(f"query={args.query}")
    print(f"hit_count={len(hits)}")
    print(
        "returned_chars="
        f"{sum(len(hit.snippet) for hit in hits)}"
    )
    for index, hit in enumerate(hits, start=1):
        prefix = f"hit_{index}"
        print(f"{prefix}_event_id={hit.event_id}")
        print(f"{prefix}_session_id={hit.session_id}")
        print(f"{prefix}_source={hit.source}")
        print(f"{prefix}_role={hit.role or ''}")
        print(f"{prefix}_category={hit.category}")
        print(f"{prefix}_chunk_path={hit.chunk_path}")
        print(
            f"{prefix}_raw_ref="
            f"{json.dumps(hit.raw_ref, ensure_ascii=False, sort_keys=True)}"
        )
        print(f"{prefix}_snippet={hit.snippet}")
    return EXIT_OK


def choose_session(
    target: Path,
    requested_id: str | None,
) -> tuple[Path, dict]:
    if requested_id:
        return find_session(target, requested_id)
    candidates = []
    for session_dir in session_dirs(target) or ():
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        candidates.append((session_dir, manifest))
    if len(candidates) != 1:
        raise ValueError(
            "show 未指定 --session-id，且目标中不只有一个 Session"
        )
    return candidates[0]


def bounded_content(text: str, budget_chars: int) -> tuple[str, bool]:
    if budget_chars <= 0:
        raise ValueError("budget_chars 必须大于 0")
    if len(text) <= budget_chars:
        return text, False
    return text[:budget_chars], True


def command_show(args: argparse.Namespace) -> int:
    target = target_from(args)
    session_dir, manifest = choose_session(target, args.session_id)
    if args.event_id:
        db_path = target / "index" / "context.sqlite3"
        with closing(sqlite3.connect(db_path)) as connection:
            row = connection.execute(
                """
                SELECT event_id, category, role, text, chunk_path, raw_ref
                FROM events
                WHERE session_id = ? AND event_id = ?
                """,
                (manifest["session_id"], args.event_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"未找到事件: {args.event_id}")
        payload = json.dumps(
            {
                "event_id": row[0],
                "category": row[1],
                "role": row[2],
                "text": row[3],
                "chunk_path": row[4],
                "raw_ref": json.loads(row[5]),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        kind = "event"
        path_text = str(db_path)
    elif args.chunk:
        relative = Path(args.chunk)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("--chunk 必须是 Session 内相对路径")
        path = (session_dir / relative).resolve()
        context_dir = (session_dir / "context").resolve()
        if context_dir not in path.parents or not path.is_file():
            raise ValueError(f"Chunk 路径无效: {args.chunk}")
        payload = path.read_text(encoding="utf-8")
        kind = "chunk"
        path_text = str(path)
    else:
        path = session_dir / "INDEX.md"
        if not path.is_file():
            raise ValueError(f"Session Index 不存在: {path}")
        payload = path.read_text(encoding="utf-8")
        kind = "session_index"
        path_text = str(path)
    content, truncated = bounded_content(payload, args.budget_chars)
    print(f"kind={kind}")
    print(f"session_id={manifest['session_id']}")
    print(f"path={path_text}")
    print(f"truncated={bool_text(truncated)}")
    print("content_begin")
    print(content)
    print("content_end")
    return EXIT_OK


def command_rebuild(args: argparse.Namespace) -> int:
    result = rebuild_archive(target_from(args))
    for key, value in result.items():
        print(f"{key}={value}")
    print("derived_health=ready")
    return EXIT_OK


def command_verify(args: argparse.Namespace) -> int:
    result = verify_archive(
        target_from(args),
        session_id=args.session_id,
    )
    print(f"verified={bool_text(result.verified)}")
    print(f"issue_count={len(result.issues)}")
    print(f"sessions={result.sessions}")
    print(f"raw_entries={result.raw_entries}")
    print(f"events={result.events}")
    print(f"chunks={result.chunks}")
    for index, issue in enumerate(result.issues, start=1):
        print(f"issue_{index}={issue}")
    return EXIT_OK if result.verified else EXIT_VERIFY


def command_merge(args: argparse.Namespace) -> int:
    if not args.legacy:
        print(
            "error=merge 只处理旧布局，必须显式传入 --legacy",
            file=sys.stderr,
        )
        return EXIT_ARGUMENT
    result = merge_legacy(target_from(args))
    for key, value in result.items():
        print(f"{key}={value}")
    print("inputs_preserved=true")
    return EXIT_OK


def command_compress(args: argparse.Namespace) -> int:
    output = compress_legacy(args.input, args.output)
    print(f"output={output}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcript_manager.py",
        description=(
            "Context Logger v2：精确 Session、Raw、Markdown 与 FTS5"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve", help="只读解析 Session 与目标")
    add_resolution_arguments(resolve)
    resolve.set_defaults(handler=command_resolve)

    anchor = subparsers.add_parser(
        "anchor",
        help="静默保存 Claude Code SessionStart 精确锚点",
    )
    anchor.add_argument("--anchors-dir", type=Path)
    anchor.set_defaults(handler=command_anchor)

    save = subparsers.add_parser("save", help="保存并重建当前 Session")
    add_resolution_arguments(save)
    save.set_defaults(handler=command_save)

    status = subparsers.add_parser("status", help="显示模块归档状态")
    status.add_argument("--target-dir", type=Path, required=True)
    status.set_defaults(handler=command_status)

    search = subparsers.add_parser("search", help="FTS5 全文检索")
    search.add_argument("--target-dir", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--session-id")
    search.add_argument("--source")
    search.add_argument("--role")
    search.add_argument("--category")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--budget-chars", type=int, default=12000)
    search.set_defaults(handler=command_search)

    show = subparsers.add_parser("show", help="展示 Session、Chunk 或事件")
    show.add_argument("--target-dir", type=Path, required=True)
    show.add_argument("--session-id")
    group = show.add_mutually_exclusive_group()
    group.add_argument("--chunk")
    group.add_argument("--event-id")
    show.add_argument("--budget-chars", type=int, default=12000)
    show.set_defaults(handler=command_show)

    rebuild = subparsers.add_parser(
        "rebuild-index",
        help="从 Raw/Manifest 重建全部派生层",
    )
    rebuild.add_argument("--target-dir", type=Path, required=True)
    rebuild.set_defaults(handler=command_rebuild)

    verify = subparsers.add_parser("verify", help="核验归档各层一致性")
    verify.add_argument("--target-dir", type=Path, required=True)
    verify.add_argument("--session-id")
    verify.set_defaults(handler=command_verify)

    compress = subparsers.add_parser(
        "compress",
        help="兼容性生成旧 JSONL 的确定性预览",
    )
    compress.add_argument("input", type=Path)
    compress.add_argument("output", type=Path, nargs="?")
    compress.set_defaults(handler=command_compress)

    merge = subparsers.add_parser(
        "merge",
        help="显式、非破坏地整理旧平铺归档",
    )
    merge.add_argument("--target-dir", type=Path, required=True)
    merge.add_argument("--legacy", action="store_true")
    merge.set_defaults(handler=command_merge)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except UnsupportedSourceError as error:
        print(f"error={error}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    except ResolutionConflictError as error:
        print(f"error={error}", file=sys.stderr)
        return EXIT_CONFLICT
    except RawWriteError as error:
        print(f"error={error}", file=sys.stderr)
        return EXIT_RAW
    except DerivedLayerError as error:
        print(f"error={error}", file=sys.stderr)
        print("raw_saved=true", file=sys.stderr)
        print("derived_health=needs_rebuild", file=sys.stderr)
        return EXIT_DERIVED
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        print(f"error={error}", file=sys.stderr)
        return EXIT_ARGUMENT
    except sqlite3.Error as error:
        print(f"error={error}", file=sys.stderr)
        return EXIT_DERIVED


if __name__ == "__main__":
    raise SystemExit(main())
