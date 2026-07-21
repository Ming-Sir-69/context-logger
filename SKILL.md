---
name: context-logger
description: 保存或恢复当前 Codex、Claude Code 开发 Session。用户说「记录上下文」「保存上下文」「存档对话」「记录对话」「存个档」「上下文快照」「save context」「checkpoint」或要求从已归档 Session 恢复开发上下文时使用；只有明确要求整理旧归档时才使用 merge --legacy。
---

# Context Logger

## 保存前必须精确解析

每次保存先运行只读 `resolve`。必须取得：

- 当前来源与 Session ID；
- `complete` 完整性；
- `exact_session` 或用户明确提供文件形成的 `explicit_session_file`；
- 当前任务工作区；
- 已确认模块及其 `project_context/transcripts`。

Codex 优先使用当前 Thread ID：

```bash
python3 <Skill目录>/scripts/transcript_manager.py resolve \
  --source codex \
  --session-id <当前Thread ID> \
  --project-root <工作区绝对路径> \
  --module-id <已登记模块ID>
```

Claude Code 优先使用当前 Session 的 Hook 环境锚点或 Session ID。没有精确锚点时
使用显式 `--session-file`，不得选择“最新 Claude Session”。

如果根目录存在 `workspace.json` 且
`context_policy=require_registered_module`，必须命中登记模块。显式
`--target-dir` 也必须等于登记模块的 Transcript 路径。

来源、Session、工作区、模块或目标任一项冲突时停止，不写入。

## 保存

确认 `resolve` 后，用完全相同的来源、Session、工作区、模块和目标参数执行
`save`。不得在两条命令之间切换目标。

保存后报告：

- `new_raw_entries` 和 `total_raw_entries`；
- Session 目录；
- Normalized 事件数和 Markdown Chunk 数；
- `derived_health`；
- 任何降级或失败。

派生层失败且 `raw_saved=true` 时，不要重复猜测其他 Session。使用同一目标执行
`rebuild-index`，随后运行 `verify`。

## 恢复上下文

1. 先读取模块 `INDEX.md`；
2. 使用 `search --query ... --budget-chars ...` 检索；
3. 根据命中的 Session、Chunk、事件和 Raw 引用判断相关性；
4. 使用 `show` 读取一个 Session Index、一个 Chunk 或一个事件；
5. 只有证据不足时再扩大查询或字符预算。

禁止直接读取整个 Transcript 目录或一次加载所有 Raw。

## 核验

完成保存或重建后运行 `verify`。只有同时出现：

```text
verified=true
issue_count=0
```

才可以报告归档闭环通过。

## 旧布局

`status` 可以识别旧平铺 `raw/`、`compressed/` 和 `.transcript_state`，但不得自动
移动、删除或覆盖。只有用户明确要求整理旧归档时才执行：

```bash
python3 <Skill目录>/scripts/transcript_manager.py merge \
  --legacy \
  --target-dir <已确认目标>
```

## 来源边界

- Raw 是事实源，不得重新序列化或注入字段；
- Claude Cowork 不是 Claude Code；完整转录未经验证时只报告 `unsupported`；
- Cowork 失败时不得回退到最新 Claude Code Session；
- ChatGPT 普通聊天与账户级导出不属于本 Skill 当前保存范围；
- Transcript 可能包含敏感信息，不复制凭证或大段 Raw 到回复、日志或公开仓库。
