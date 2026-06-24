#!/usr/bin/env python3
"""
Transcript Manager — Context Logger 核心脚本
支持 Claude Code 和 Codex 两种环境，自动检测来源。

命令:
  save      增量保存当前 session 的新条目（Entry ID 去重），同时产出 JSONL + 压缩 MD
  compress  将指定 JSONL 压缩为精简 Markdown
  merge     合并所有 JSONL 去重 → 单个干净 JSONL + 压缩 MD
  status    查看当前 session 和存档状态

设计原则:
  - 增量基于 Entry ID 去重（兼容 Claude uuid 与 Codex 复合键），不是行数 tail
  - save 总是产出两类文件：raw JSONL 增量 + 压缩 MD 增量
  - merge 在需要"干净起点"时手动触发，合并后重置基线
  - 自动标记 _source 字段（codex/claude），便于跨环境交叉识别

用法:
  python3 transcript_manager.py save   [--project-root /path/to/project]
  python3 transcript_manager.py compress <input.jsonl> [output.md]
  python3 transcript_manager.py merge   [--project-root /path/to/project]
  python3 transcript_manager.py status  [--project-root /path/to/project]
"""

import json
import os
import sys
import shutil
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# ─── 全局配置 ────────────────────────────────────────
CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")
TOOL_RESULT_MAX_CHARS = 300
TOOL_INPUT_MAX_CHARS = 200
KEEP_THINKING = False

# ─── 运行环境检测 ────────────────────────────────────
def _detect_source():
    """自动检测当前运行环境，用于 _source 字段标记"""
    if os.path.exists(os.path.expanduser("~/.codex/.codex-global-state.json")):
        return "codex"
    return "claude"

SOURCE = _detect_source()


# ═══════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════

def ts_now():
    return datetime.now(CST).strftime('%Y-%m-%d_%H%M')


def ts_to_str(ts):
    """ISO timestamp → 北京时间友好格式"""
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.astimezone(CST).strftime('%m-%d %H:%M')
    except:
        return ts[:16] if len(ts) > 16 else ts


def find_project_root():
    """从当前目录向上查找项目根"""
    cwd = os.getcwd()
    if '/item_fille/' in cwd:
        parts = cwd.split('/item_fille/')
        return parts[0]
    return cwd


def resolve_target_dir(project_root):
    """确定存档存放目录（项目自身目录下的 project_context/transcripts/）

    优先级:
    1. cwd 在 item_fille/<项目>/ 子目录内 → 直接定位
       (此为原项目结构约定，可改造。如需自定义项目目录，使用 --project-root 参数)
    2. 扫描 item_fille/ 下所有项目，选最近活跃的
    3. fallback: 返回 None
    """
    cwd = os.getcwd()

    # 优先级 1: cwd 就在 item_fille/xxx 下
    if '/item_fille/' in cwd:
        idx = cwd.index('/item_fille/')
        rest = cwd[idx + 1:]
        parts = rest.split('/')
        if len(parts) >= 2:
            project_path = os.path.join(cwd[:idx], parts[0], parts[1])
            target = os.path.join(project_path, 'project_context', 'transcripts')
            if os.path.isdir(os.path.join(project_path, 'project_context')):
                return target

    # 优先级 2: 扫描 item_fille/ 下所有项目，找最近活跃的
    item_fille_dir = os.path.join(project_root, 'item_fille')
    if os.path.isdir(item_fille_dir):
        best_mtime = 0
        best_target = None
        for entry in os.listdir(item_fille_dir):
            if entry.startswith('.') or entry == '00_error':
                continue
            proj_path = os.path.join(item_fille_dir, entry)
            if not os.path.isdir(proj_path):
                continue
            pc_dir = os.path.join(proj_path, 'project_context')
            if not os.path.isdir(pc_dir):
                continue
            signals = [
                os.path.join(proj_path, 'HANDOFF.md'),
                pc_dir,
            ]
            proj_mtime = 0
            for s in signals:
                if os.path.exists(s):
                    proj_mtime = max(proj_mtime, os.path.getmtime(s))
            if proj_mtime > best_mtime:
                best_mtime = proj_mtime
                best_target = os.path.join(pc_dir, 'transcripts')

        if best_target:
            return best_target

    return None


def find_workspace_root():
    """找到当前工作区的根目录"""
    cwd = os.getcwd()
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True, text=True, cwd=cwd
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    if '/item_fille/' in cwd:
        idx = cwd.index('/item_fille/')
        return cwd[:idx]
    return cwd


def get_claude_jsonl_dir():
    """获取当前工作区的 Claude Code JSONL 存储目录"""
    workspace_root = find_workspace_root()
    workspace_key = workspace_root.lstrip('/').replace('/', '-')
    jsonl_dir = os.path.join(CLAUDE_PROJECTS_DIR, workspace_key)
    if not os.path.isdir(jsonl_dir):
        alt_keys = [workspace_root.replace('/', '-'),
                    '-' + workspace_root.replace('/', '-')]
        for k in alt_keys:
            alt_dir = os.path.join(CLAUDE_PROJECTS_DIR, k)
            if os.path.isdir(alt_dir):
                return alt_dir
        return None
    return jsonl_dir


def get_codex_jsonl_dir():
    """获取 Codex 的 JSONL 存储目录"""
    if not os.path.isdir(CODEX_SESSIONS_DIR):
        return None
    return CODEX_SESSIONS_DIR


def find_latest_codex_session():
    """找到 Codex 最新的 session JSONL 文件"""
    if not os.path.isdir(CODEX_SESSIONS_DIR):
        return None
    latest = None
    latest_mtime = 0
    for root, dirs, files in os.walk(CODEX_SESSIONS_DIR):
        for f in files:
            if f.endswith('.jsonl') and f.startswith('rollout-'):
                fpath = os.path.join(root, f)
                mtime = os.path.getmtime(fpath)
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest = fpath
    return latest


def get_entry_id(entry):
    """从条目中提取唯一标识符（兼容 Claude 和 Codex 格式）

    Claude 格式: 有 uuid 字段
    Codex 格式: timestamp + type + payload_type + role + turn_id 复合键
    """
    uid = entry.get('uuid', '')
    if uid:
        return uid
    ts = entry.get('timestamp', '')
    etype = entry.get('type', '')
    payload_type = entry.get('payload', {}).get('type', '')
    role = entry.get('payload', {}).get('role', '')
    turn_id = entry.get('payload', {}).get('turn_id', '')
    return f"{ts}|{etype}|{payload_type}|{role}|{turn_id}"


def safe_output_str(output):
    """将 function_call_output.output（str/list/dict）统一转换为安全字符串"""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts = []
        for item in output:
            if isinstance(item, dict):
                parts.append(item.get('text', json.dumps(item, ensure_ascii=False)))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(str(item))
        return ' '.join(parts)
    if isinstance(output, dict):
        return json.dumps(output, ensure_ascii=False)
    return str(output)


def latest_session_file(jsonl_dir):
    """返回最新的 session JSONL 文件的完整路径"""
    files = [f for f in os.listdir(jsonl_dir) if f.endswith('.jsonl')]
    if not files:
        return None
    files.sort(key=lambda f: os.path.getmtime(os.path.join(jsonl_dir, f)), reverse=True)
    return os.path.join(jsonl_dir, files[0])


def load_entry_ids(jsonl_path):
    """加载 JSONL 文件中所有条目的 entry_id 集合（兼容双格式）"""
    ids = set()
    if not os.path.exists(jsonl_path):
        return ids
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                eid = get_entry_id(d)
                if eid:
                    ids.add(eid)
            except json.JSONDecodeError:
                continue
    return ids


def load_state(state_path):
    """加载存档状态文件"""
    if not os.path.exists(state_path):
        return {'session_id': '', 'id_count': 0}
    state = {'session_id': '', 'id_count': 0}
    with open(state_path, 'r') as f:
        for line in f:
            line = line.strip()
            if '=' in line:
                k, v = line.split('=', 1)
                state[k] = v
    state['id_count'] = int(state.get('id_count', 0))
    return state


def save_state(state_path, session_id, id_count):
    """保存存档状态"""
    with open(state_path, 'w') as f:
        f.write(f"SESSION_ID={session_id}\n")
        f.write(f"ID_COUNT={id_count}\n")
        f.write(f"LAST_SAVE={ts_now()}\n")


def count_lines(path):
    with open(path, 'r') as f:
        return sum(1 for _ in f)


# ═══════════════════════════════════════════════════════
#  save 命令
# ═══════════════════════════════════════════════════════

def cmd_save(target_dir):
    """增量保存：只存新条目 → JSONL + 压缩 MD（自动注入 _source 环境标记）"""

    # 优先尝试 Codex 格式
    latest = find_latest_codex_session()

    if not latest:
        jsonl_dir = get_claude_jsonl_dir()
        if not jsonl_dir:
            print("❌ 找不到 Codex 或 Claude Code session 目录")
            return 1
        latest = latest_session_file(jsonl_dir)

    if not latest:
        print("❌ 找不到当前 session 文件")
        return 1

    curr_session_id = os.path.basename(latest).replace('.jsonl', '')[:32]
    state_path = os.path.join(target_dir, '.transcript_state')

    raw_dir = os.path.join(target_dir, 'raw')
    comp_dir = os.path.join(target_dir, 'compressed')
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(comp_dir, exist_ok=True)

    state = load_state(state_path)
    saved_ids = set()

    for f in os.listdir(raw_dir):
        if f.endswith('.jsonl'):
            saved_ids |= load_entry_ids(os.path.join(raw_dir, f))

    new_entries = []
    with open(latest, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = get_entry_id(d)
            if eid and eid not in saved_ids:
                d['_source'] = SOURCE
                new_entries.append(json.dumps(d, ensure_ascii=False))

    ts = ts_now()
    total_ids = len(saved_ids) + len(new_entries)
    new_id_count = 0
    for raw_line in new_entries:
        try:
            d = json.loads(raw_line)
            if get_entry_id(d):
                new_id_count += 1
        except json.JSONDecodeError:
            pass

    if not new_entries:
        print(f"📭 没有新条目（已存档 {len(saved_ids)} 个 ID，当前 session 无新内容）")
        save_state(state_path, curr_session_id, total_ids)
        return 0

    incr_name = f"transcript_{ts}_incremental"
    jsonl_path = os.path.join(raw_dir, f"{incr_name}.jsonl")
    with open(jsonl_path, 'w', encoding='utf-8') as f:
        for line in new_entries:
            f.write(line + '\n')

    jsonl_size = os.path.getsize(jsonl_path)

    md_path = os.path.join(comp_dir, f"{incr_name}_compressed.md")
    try:
        compress_jsonl_lines(new_entries, md_path, os.path.basename(jsonl_path))
        md_size = os.path.getsize(md_path)
    except Exception as exc:
        print(f"⚠️ 压缩摘要生成异常: {exc}")
        print(f"   原始 JSONL 已安全保存: {jsonl_path}")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# {incr_name}_compressed\n\n压缩失败: {exc}\n原始数据: {jsonl_path}\n")
        md_size = os.path.getsize(md_path)

    save_state(state_path, curr_session_id, total_ids)

    print(f"✅ 增量保存完成")
    print(f"   新增: {new_id_count} 条 (共 {len(new_entries)} 行)")
    print(f"   来源: {SOURCE}")
    print(f"   JSONL: {jsonl_path} ({jsonl_size/1024:.1f} KB)")
    print(f"   MD:    {md_path} ({md_size/1024:.1f} KB)")
    print(f"   累计存档: {total_ids} ID")
    return 0


# ═══════════════════════════════════════════════════════
#  merge 命令
# ═══════════════════════════════════════════════════════

def cmd_merge(target_dir):
    """合并所有 raw JSONL → 去重单一 JSONL + 压缩 MD"""

    raw_dir = os.path.join(target_dir, 'raw')
    comp_dir = os.path.join(target_dir, 'compressed')
    state_path = os.path.join(target_dir, '.transcript_state')

    if not os.path.isdir(raw_dir):
        print("❌ raw 目录不存在")
        return 1

    jsonl_files = sorted([
        f for f in os.listdir(raw_dir) if f.endswith('.jsonl')
    ])
    if not jsonl_files:
        print("❌ 没有 JSONL 文件可合并")
        return 1

    os.makedirs(comp_dir, exist_ok=True)

    seen_ids = set()
    merged_entries = []
    total = 0
    dup = 0

    for fname in jsonl_files:
        path = os.path.join(raw_dir, fname)
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                eid = get_entry_id(d)
                if eid and eid in seen_ids:
                    dup += 1
                    continue
                if eid:
                    seen_ids.add(eid)
                merged_entries.append(line)

    ts = ts_now()

    merged_jsonl = os.path.join(raw_dir, f"transcript_{ts}_merged.jsonl")
    with open(merged_jsonl, 'w', encoding='utf-8') as f:
        for line in merged_entries:
            f.write(line + '\n')

    jsonl_size = os.path.getsize(merged_jsonl)

    merged_md = os.path.join(comp_dir, f"transcript_{ts}_merged_compressed.md")
    compress_jsonl_lines(merged_entries, merged_md)

    md_size = os.path.getsize(merged_md)

    archive_dir = os.path.join(target_dir, 'raw', '_archive')
    os.makedirs(archive_dir, exist_ok=True)
    for fname in jsonl_files:
        shutil.move(os.path.join(raw_dir, fname), os.path.join(archive_dir, fname))

    # 获取当前 session ID
    sess_id = ''
    cx = find_latest_codex_session()
    if cx:
        sess_id = os.path.basename(cx).replace('.jsonl', '')[:32]
    else:
        jdir = get_claude_jsonl_dir()
        if jdir:
            latest = latest_session_file(jdir)
            if latest:
                sess_id = os.path.basename(latest).replace('.jsonl', '')
    save_state(state_path, sess_id, len(seen_ids))

    print(f"✅ 合并完成")
    print(f"   原始: {len(jsonl_files)} 个文件 / {total} 条（去重 {dup}，合并后 {len(merged_entries)} 条）")
    print(f"   JSONL: {merged_jsonl} ({jsonl_size/1024:.1f} KB)")
    print(f"   MD:    {merged_md} ({md_size/1024:.1f} KB)")
    print(f"   旧文件: → raw/_archive/")
    return 0


# ═══════════════════════════════════════════════════════
#  status 命令
# ═══════════════════════════════════════════════════════

def cmd_status(target_dir):
    """打印存档状态"""
    state_path = os.path.join(target_dir, '.transcript_state')
    raw_dir = os.path.join(target_dir, 'raw')
    comp_dir = os.path.join(target_dir, 'compressed')

    print("═══ Transcript 存档状态 ═══")
    print(f"项目目录: {target_dir}")

    codex_latest = find_latest_codex_session()
    if codex_latest:
        line_count = count_lines(codex_latest)
        print(f"\n📡 当前 Session (Codex):")
        print(f"   文件: {os.path.basename(codex_latest)}")
        print(f"   行数: {line_count}")
        print(f"   路径: {codex_latest}")
    else:
        jsonl_dir = get_claude_jsonl_dir()
        if jsonl_dir:
            latest = latest_session_file(jsonl_dir)
            if latest:
                curr_session = os.path.basename(latest).replace('.jsonl', '')
                line_count = count_lines(latest)
                print(f"\n📡 当前 Session (Claude):")
                print(f"   ID:   {curr_session[:16]}...")
                print(f"   行数: {line_count}")
                print(f"   路径: {latest}")

    if os.path.exists(state_path):
        state = load_state(state_path)
        print(f"\n📋 上次存档:")
        print(f"   Session ID:  {state['session_id'][:16] if state['session_id'] else '(无)'}...")
        print(f"   已存档 ID: {state['id_count']}")
        print(f"   保存时间:    {state.get('last_save', '?')}")

    for label, d in [('JSONL 存档', raw_dir), ('MD 压缩', comp_dir)]:
        if os.path.isdir(d):
            files = [f for f in os.listdir(d) if not f.startswith('.') and not f.startswith('_')]
            total_kb = sum(os.path.getsize(os.path.join(d, f)) for f in files if os.path.isfile(os.path.join(d, f))) / 1024
            print(f"\n📁 {label} ({len(files)} 个文件, {total_kb:.0f} KB):")
            for f in sorted(files)[-5:]:
                size = os.path.getsize(os.path.join(d, f)) / 1024
                print(f"   {f} ({size:.0f} KB)")

    codex_latest = find_latest_codex_session()
    if codex_latest:
        curr_id_count = len(load_entry_ids(codex_latest))
        saved_ids = set()
        if os.path.isdir(raw_dir):
            for f in os.listdir(raw_dir):
                if f.endswith('.jsonl') and not f.startswith('_'):
                    saved_ids |= load_entry_ids(os.path.join(raw_dir, f))
        new_count = curr_id_count - len(saved_ids)
        if new_count > 0:
            print(f"\n⏳ 待保存: ~{new_count} 条新条目（估算）")
        else:
            print(f"\n✅ 已同步，无待保存条目")
    else:
        jsonl_dir = get_claude_jsonl_dir()
        if jsonl_dir:
            latest = latest_session_file(jsonl_dir)
            if latest:
                curr_id_count = len(load_entry_ids(latest))
                saved_ids = set()
                if os.path.isdir(raw_dir):
                    for f in os.listdir(raw_dir):
                        if f.endswith('.jsonl') and not f.startswith('_'):
                            saved_ids |= load_entry_ids(os.path.join(raw_dir, f))
                new_count = curr_id_count - len(saved_ids)
                if new_count > 0:
                    print(f"\n⏳ 待保存: ~{new_count} 条新条目（估算）")
                else:
                    print(f"\n✅ 已同步，无待保存条目")

    return 0


# ═══════════════════════════════════════════════════════
#  compress 命令
# ═══════════════════════════════════════════════════════

def cmd_compress(input_path, output_path=None):
    """压缩单个 JSONL 为 Markdown"""
    if not output_path:
        base = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(
            os.path.dirname(input_path) or '.',
            f"{base}_compressed.md"
        )

    with open(input_path, 'r', encoding='utf-8') as f:
        entries = [line.strip() for line in f if line.strip()]

    compress_jsonl_lines(entries, output_path)

    in_size = os.path.getsize(input_path)
    out_size = os.path.getsize(output_path)
    ratio = in_size / out_size if out_size > 0 else 0
    print(f"✅ 压缩完成: {in_size/1024:.0f} KB → {out_size/1024:.0f} KB ({ratio:.0f}:1)")
    print(f"   输出: {output_path}")
    return 0


# ═══════════════════════════════════════════════════════
#  Codex 格式压缩引擎
# ═══════════════════════════════════════════════════════

def compress_codex_jsonl(entries, output_path, source_name="input.jsonl"):
    """从 Codex JSONL 条目生成压缩 Markdown（TypeError 防御版）"""
    lines = []
    lines.append("# 对话转录压缩摘要 (Codex)")
    lines.append(f"> 来源: `{source_name}`")
    lines.append(f"> 压缩时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M')} CST")
    lines.append(f"> 条目数: {len(entries)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    DECISION_KEYWORDS = [
        '决定', '方案', '架构', '设计', '选型', '改为', '改用', '重构',
        '问题', '报错', '错误', '失败', '修复', '解决', '放弃',
        'PRD', '需求', 'Phase', 'Milestone', '部署', '上线',
        'CoreData', 'SwiftUI', 'SwiftData', 'MVVM', 'API',
        'Route', '预算', '验收', '终止', '冻结',
    ]

    turn_num = 0
    for e in entries:
        etype = e.get('type', '')
        payload = e.get('payload', {})
        ts = e.get('timestamp', '')
        payload_type = payload.get('type', '')

        if etype in ('session_meta', 'turn_context'):
            continue
        if etype == 'event_msg' and payload_type in ('token_count', 'task_started'):
            continue

        if etype == 'response_item':
            role = payload.get('role', '')
            content_list = payload.get('content', []) or []

            if payload_type == 'message' and role == 'user':
                for item in content_list:
                    if item.get('type') == 'input_text':
                        text = item.get('text', '')
                        if text.startswith('# AGENTS.md') or text.startswith('<permissions') or text.startswith('<environment'):
                            continue
                        if len(text) < 5:
                            continue
                        turn_num += 1
                        is_key = any(kw in text for kw in DECISION_KEYWORDS)
                        prefix = '⚡ ' if is_key else ''
                        lines.append(f"### {prefix}轮次 {turn_num} — 用户")
                        lines.append(f"*{ts_to_str(ts)}*")
                        lines.append("")
                        if len(text) > 2000:
                            text = text[:2000] + f"\n\n*(消息过长，已截断，原 {len(text)} 字符)*"
                        lines.append(text)
                        lines.append("")

            elif payload_type == 'message' and role == 'assistant':
                for item in content_list:
                    if item.get('type') == 'output_text':
                        text = item.get('text', '')
                        if len(text) > 3000:
                            text = text[:3000] + f"\n\n*(回复过长，已截断，原 {len(text)} 字符)*"
                        lines.append(f"**AI 回复:**")
                        lines.append("")
                        lines.append(text)
                        lines.append("")

            elif payload_type == 'function_call':
                name = payload.get('name', 'unknown')
                args = payload.get('arguments', '{}')
                if len(args) > 200:
                    args = args[:200] + '...'
                lines.append(f"- 🔧 `{name}`: {args}")
                lines.append("")

            elif payload_type == 'function_call_output':
                raw_output = payload.get('output', '')
                safe = safe_output_str(raw_output)
                output_str = safe[:500] + '...' if len(safe) > 500 else safe
                lines.append(f"<details><summary>📋 工具返回</summary>")
                lines.append("")
                lines.append("```")
                lines.append(output_str)
                lines.append("```")
                lines.append("</details>")
                lines.append("")

            elif payload_type == 'reasoning':
                summary = payload.get('summary', [])
                if summary:
                    text = summary[0].get('text', '') if summary else ''
                    if len(text) > 500:
                        text = text[:500] + '...'
                    lines.append(f"<details><summary>💭 推理</summary>")
                    lines.append("")
                    lines.append(text)
                    lines.append("</details>")
                    lines.append("")

        elif etype == 'event_msg':
            if payload_type == 'user_message':
                text = payload.get('text', '')
                if len(text) > 2000:
                    text = text[:2000] + f"\n\n*(消息过长，已截断，原 {len(text)} 字符)*"
                lines.append(f"**用户消息:** {text}")
                lines.append("")
            elif payload_type == 'agent_reasoning':
                text = payload.get('text', '')
                if len(text) > 500:
                    text = text[:500] + '...'
                lines.append(f"<details><summary>💭 Agent 推理</summary>")
                lines.append("")
                lines.append(text)
                lines.append("</details>")
                lines.append("")

    lines.append("---")
    lines.append("")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


# ═══════════════════════════════════════════════════════
#  压缩引擎（自动格式检测）
# ═══════════════════════════════════════════════════════

def compress_jsonl_lines(lines, output_path, source_name="input.jsonl"):
    """从 JSONL 行列表生成压缩 Markdown（自动检测格式）"""
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if entries and entries[0].get('type') in ('session_meta', 'response_item', 'turn_context', 'event_msg'):
        compress_codex_jsonl(entries, output_path, source_name)
        return

    compress_jsonl_entries(lines, output_path, source_name)


def compress_jsonl_entries(raw_lines, output_path, source_name="input.jsonl"):
    """从 JSONL 原始行列表生成压缩 Markdown（Claude 格式）"""
    entries = []
    for line in raw_lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    lines = []
    lines.append("# 对话转录压缩摘要")
    lines.append(f"> 来源: `{source_name}`")
    lines.append(f"> 压缩时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M')} CST")
    lines.append(f"> 条目数: {len(entries)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    sessions = {}
    for e in entries:
        sid = e.get('sessionId', 'unknown')
        if sid not in sessions:
            sessions[sid] = []
        sessions[sid].append(e)

    DECISION_KEYWORDS = [
        '决定', '方案', '架构', '设计', '选型', '改为', '改用', '重构',
        '问题', '报错', '错误', '失败', '修复', '解决', '放弃',
        'PRD', '需求', 'Phase', 'Milestone', '部署', '上线',
        'CoreData', 'SwiftUI', 'SwiftData', 'MVVM', 'API',
    ]

    def session_first_ts(sid):
        for e in sessions[sid]:
            ts = e.get('timestamp', '')
            if ts:
                return ts
        return 'z'

    sorted_sessions = sorted(sessions.items(), key=lambda x: session_first_ts(x[0]))

    for sid, session_entries in sorted_sessions:
        title = None
        for e in session_entries:
            if e.get('type') == 'ai-title':
                title = e.get('aiTitle', '')
                break

        timestamps = [e.get('timestamp', '') for e in session_entries if e.get('timestamp')]
        first_ts = min(timestamps) if timestamps else '?'
        last_ts = max(timestamps) if timestamps else '?'

        lines.append(f"## 会话: {title or '(无标题)'}")
        lines.append(f"**时间**: {ts_to_str(first_ts)} → {ts_to_str(last_ts)}")
        lines.append(f"**Session ID**: `{sid[:8]}...`")
        lines.append(f"**条目数**: {len(session_entries)}")
        lines.append("")

        turn_num = 0
        entry_idx = 0
        while entry_idx < len(session_entries):
            e = session_entries[entry_idx]

            if _is_noise(e):
                entry_idx += 1
                continue

            t = e.get('type', '')

            if t == 'user':
                contents = _extract_user_content(e)
                if not contents:
                    entry_idx += 1
                    continue

                turn_num += 1
                all_text = ' '.join(c[1] for c in contents if c[0] == 'text')
                is_key = any(kw in all_text for kw in DECISION_KEYWORDS)
                prefix = '⚡ ' if is_key else ''

                lines.append(f"### {prefix}轮次 {turn_num} — 用户")
                lines.append(f"*{ts_to_str(e.get('timestamp', ''))}*")
                lines.append("")

                for ctype, ctext in contents:
                    if ctype == 'text':
                        if len(ctext) > 2000:
                            ctext = ctext[:2000] + f"\n\n*(消息过长，已截断，原 {len(ctext)} 字符)*"
                        lines.append(f"{ctext}")
                        lines.append("")
                    elif ctype == 'tool_result':
                        lines.append(f"<details><summary>📋 工具结果 ({len(ctext):,} 字符)</summary>\n")
                        lines.append("```")
                        lines.append(_summarize_tool_result(ctext))
                        lines.append("```")
                        lines.append("</details>")
                        lines.append("")

            elif t == 'assistant':
                contents = _extract_assistant_content(e)
                if not contents:
                    entry_idx += 1
                    continue

                has_text = any(c[0] == 'text' for c in contents)
                if not has_text:
                    tool_calls = [c for c in contents if c[0] == 'tool_use']
                    if tool_calls:
                        calls_str = ', '.join(
                            f"`{tc[1].get('name','?')}`" for tc in tool_calls[:5]
                        )
                        if len(tool_calls) > 5:
                            calls_str += f' ... 等 {len(tool_calls)} 个调用'
                        lines.append(f"*({calls_str})*")
                        lines.append("")
                    entry_idx += 1
                    continue

                lines.append(f"**AI 回复:**")
                lines.append("")

                for ctype, ctext in contents:
                    if ctype == 'text':
                        if len(ctext) > 3000:
                            ctext = ctext[:3000] + f"\n\n*(回复过长，已截断，原 {len(ctext)} 字符)*"
                        lines.append(ctext)
                        lines.append("")
                    elif ctype == 'thinking' and KEEP_THINKING:
                        lines.append(f"<details><summary>💭 思考过程</summary>\n")
                        think_text = ctext[:150] + '...' if len(ctext) > 150 else ctext
                        lines.append(think_text)
                        lines.append("\n</details>\n")
                    elif ctype == 'tool_use':
                        name = ctext.get('name', 'unknown')
                        inp = json.dumps(ctext.get('input', {}), ensure_ascii=False)
                        if len(inp) > TOOL_INPUT_MAX_CHARS:
                            inp = inp[:TOOL_INPUT_MAX_CHARS] + '...'
                        lines.append(f"- 🔧 `{name}`: {inp}")

                if any(c[0] == 'tool_use' for c in contents):
                    lines.append("")

            entry_idx += 1

        lines.append("---")
        lines.append("")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


# ── 压缩引擎内部函数 ─────────────────────────────────

def _is_noise(entry):
    noise_types = {'queue-operation', 'mode', 'file-history-snapshot', 'last-prompt', 'system'}
    t = entry.get('type', '')
    if t in noise_types:
        return True
    if t == 'attachment':
        att = entry.get('attachment', {})
        if att.get('type') == 'skill_listing':
            return True
    return False


def _extract_user_content(message):
    content = message.get('message', {}).get('content', [])
    if isinstance(content, str):
        return [('text', content)]
    results = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get('type') == 'text':
            text = c.get('text', '')
            if text.startswith('<ide_opened_file>') or text.startswith('<system-reminder>'):
                continue
            results.append(('text', text))
        elif c.get('type') == 'tool_result':
            raw = c.get('content', '')
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        results.append(('tool_result', item.get('text', '')))
                    elif isinstance(item, str):
                        results.append(('tool_result', item))
            elif isinstance(raw, str):
                results.append(('tool_result', raw))
    return results


def _extract_assistant_content(message):
    content = message.get('message', {}).get('content', [])
    if isinstance(content, str):
        return [('text', content)]
    results = []
    for c in content:
        if not isinstance(c, dict):
            continue
        t = c.get('type', '')
        if t == 'text':
            results.append(('text', c.get('text', '')))
        elif t == 'thinking' and KEEP_THINKING:
            results.append(('thinking', c.get('thinking', '')))
        elif t == 'tool_use':
            results.append(('tool_use', c))
    return results


def _summarize_tool_result(text):
    if not text:
        return "(空)"
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    head = text[:TOOL_RESULT_MAX_CHARS // 2]
    tail = text[-(TOOL_RESULT_MAX_CHARS // 2):]
    return f"{head}\n... (共 {len(text):,} 字符，已截断) ...\n{tail}"


# ═══════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]

    project_root = None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '--project-root' and i + 1 < len(args):
            project_root = args[i + 1]
            i += 2
        else:
            i += 1

    if project_root is None:
        project_root = find_project_root()

    target_dir = resolve_target_dir(project_root)

    if target_dir is None:
        print("❌ 无法确定项目目录。请将终端 cd 到项目目录内再执行，或使用 --project-root 指定。")
        print("   提示：工作区根目录的 project_context/ 仅用于跨项目规则，不应存放项目级 transcripts。")
        return 1

    if cmd == 'save':
        return cmd_save(target_dir)

    elif cmd == 'merge':
        return cmd_merge(target_dir)

    elif cmd == 'status':
        return cmd_status(target_dir)

    elif cmd == 'compress':
        non_flag_args = [a for a in sys.argv[2:] if a != '--project-root' and
                         (sys.argv.index(a) <= 2 or sys.argv[sys.argv.index(a)-1] != '--project-root')]
        if len(non_flag_args) < 2:
            print("用法: transcript_manager.py compress <input.jsonl> [output.md]")
            return 1
        input_path = non_flag_args[0]
        output_path = non_flag_args[1] if len(non_flag_args) > 2 else None
        return cmd_compress(input_path, output_path)

    else:
        print(f"未知命令: {cmd}")
        print("可用命令: save, compress, merge, status")
        return 1


if __name__ == '__main__':
    sys.exit(main())
