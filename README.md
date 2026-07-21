# Context Logger

Context Logger 将 Codex 或 Claude Code 的一个明确 Session 保存为可核验的本地上下文。
它不调用摘要模型，也不依赖向量数据库。

## 数据层

每个 Session 同时保留：

1. 来源 JSONL 的字节保真 Raw；
2. 跨宿主统一的 Normalized 事件；
3. 面向 AI 的分段 Markdown 和两级 Index；
4. 可删除、可重建的 SQLite FTS5 全文索引。

用户和 AI 正文跨 Markdown Chunk 完整保留。工具输入和 Markdown 工具结果只展示
前 2,000 字符，但始终保留 Raw 引用；工具结果在 FTS5 中最多索引 8,000 字符。

## 安装

默认安装到 Context Logger 的运行目录，并保留式注册 Claude Code
`SessionStart` 锚点：

```bash
bash install.sh
```

临时验收安装不会修改真实 Hook：

```bash
bash install.sh --target /absolute/path/to/temp-skill
```

安装器会保留 Claude 的其他设置与 Hook，并在首次修改前创建
`settings.json.context-logger.bak`。

如果现有 `settings.json` 的 `hooks` 缺失或为 `null`，安装器会停止，避免把未知的
既有登记误当成空配置。确认可信基线后可显式恢复：

```bash
bash install.sh --restore-hooks-from /absolute/path/to/hooks-baseline.json
```

基线文件必须包含非空的 `{ "hooks": { ... } }`。安装只恢复该字段，其他 Claude
设置及原文件权限保持不变。

## 精确解析

Codex 使用当前 Thread ID：

```bash
python3 scripts/transcript_manager.py resolve \
  --source codex \
  --session-id <current-thread-id> \
  --project-root /absolute/path/to/workspace \
  --module-id <registered-module-id>
```

Claude Code 的 `SessionStart` Hook 会写入不含对话正文的短期锚点。当前 Session
可以使用 Hook 注入的环境锚点，也可以显式传入 Session ID 或 Transcript 文件：

```bash
python3 scripts/transcript_manager.py resolve \
  --source claude-code \
  --session-id <current-session-id> \
  --project-root /absolute/path/to/workspace \
  --module-id <registered-module-id>
```

受管工作区若使用 `context_policy=require_registered_module`，归档目标必须命中
`workspace.json` 中登记的模块；不会在工作区根目录创建 Transcript。

## 生命周期命令

先完成 `resolve` 并检查输出，再使用相同的 Session 与目标参数执行 `save`。

```text
anchor          接收 Claude Code SessionStart JSON 并静默写入锚点
resolve         只读解析来源、Session、工作区、模块和目标
save            增量保存 Raw 并重建派生层
status          显示模块归档与旧布局状态
search          使用 SQLite FTS5 按预算检索
show            展示 Session Index、Chunk 或事件
rebuild-index   从 Raw 和 Manifest 重建全部派生层
verify          核验 Raw、Normalized、Markdown、SQLite 和 State
compress        兼容性生成旧 JSONL 的确定性预览
merge           仅在显式 --legacy 时非破坏整理旧平铺归档
```

恢复上下文时先读取模块 `INDEX.md`，再使用 `search` 定位候选，最后用 `show`
加载少量相关 Chunk。不要直接读取整个 Transcript 目录。

## 存储布局

```text
project_context/transcripts/
├── INDEX.md
├── index/context.sqlite3
└── sessions/<source>_<session-id>/
    ├── manifest.json
    ├── state.json
    ├── INDEX.md
    ├── raw/part-*.jsonl
    ├── normalized/events-*.jsonl
    └── context/chunk-NNNNNN.md
```

Raw 和 Manifest 是重建事实源。派生失败时 Raw 仍保留，`state.json` 会标记
`needs_rebuild`。只有 `verify` 返回 `verified=true` 才表示各层一致。
Context Logger 只把严格匹配 `chunk-六位数字.md` 的文件识别为正式分片；
云盘生成的 `chunk-000001 2.md` 等冲突副本不会被读取、索引、计数或自动删除。

## 来源边界

- Codex：支持 Thread ID 或显式 Session 文件；
- Claude Code：支持短期 Hook 锚点或显式 Session 文件；
- Claude Cowork：没有经过验证的完整 Transcript 时返回 `unsupported`，不会冒充
  Claude Code，也不会抓取私有接口；
- ChatGPT 普通聊天：不在本工具当前范围。

## 许可证

MIT © 2026 Eric Mingle (Ming-Sir-69)
