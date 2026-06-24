---
name: context-logger
description: 保存当前 AI 对话的完整上下文到项目 transcripts 目录。同时产出原始 JSONL 增量 + 压缩 Markdown 精要。基于双格式 Entry ID 去重，永不重复。
---

# AI 指令：Context Logger

## 触发条件

用户说出以下任一短语时立即执行：
- "记录上下文" / "保存上下文" / "存档对话" / "记录对话" / "存个档" / "上下文快照"
- "save context" / "checkpoint"
- "合并上下文" / "整理上下文"

## 执行步骤

### Step 1: 确定运行环境

检测环境中存在哪个目录来决定使用哪个脚本路径：

~/.claude（Claude Code）
~/.codex（Codex Desktop）
两者都安装时：根据当前 AI 种类选择。

### Step 2: 执行保存

```bash
python3 <脚本路径>/transcript_manager.py save
```

**说明**：脚本会自动定位项目目录（依赖 `item_fille/` 结构或 `--project-root` 参数）。如果自动定位失败，手动指定：

```bash
python3 <脚本路径>/transcript_manager.py save --project-root /当前/项目/路径
```

### Step 3: 报告结果

告知用户：
- 新增条目数 / 总存档 ID 数
- JSONL 和 MD 文件大小
- 如果碎片文件 > 10 个，建议用 `merge` 整理

## 产物存放位置

```
<项目>/project_context/transcripts/
├── .transcript_state              ← 状态文件
├── raw/                           ← 原始 JSONL
│   ├── transcript_<时间>_incremental.jsonl
│   └── _archive/
└── compressed/                    ← 压缩 Markdown
    └── transcript_<时间>_incremental_compressed.md
```

## 注意事项（AI 必须遵守）

### 关于 merge

**日常存档只用 `save` 命令。** `merge` 命令会把旧碎片文件移入 `raw/_archive/`，从 `raw/` 目录看就像"被清空"。

仅在以下条件同时满足时才考虑 `merge`：
1. 用户明确说「合并上下文」「整理上下文」
2. `raw/` 下的增量碎片文件超过 10 个
3. 先用 `status` 确认当前没有待保存的新条目

### 关于读取存档

**禁止直接 `Read` 整个 transcripts/ 目录下的文件。** JSONL 存档可能高达 52MB 以上，全量读取会撑爆上下文。

需要查看存档内容时：
1. 先用 `grep` 搜索关键词确认目标在哪个文件
2. 用 `Read offset/limit` 局部读取（一次不超过 100 行）
3. 压缩 MD 文件也需控制读取量

## 主要函数与命令参考

| 函数 | 作用 |
|------|------|
| `save` | 增量保存新条目 + 压缩 MD |
| `status` | 查看存档状态、待保存数 |
| `merge` | 合并去重（谨慎使用） |
| `compress <input> [output]` | 手动压缩 JSONL |

核心特性：
- **双格式去重**：兼容 Claude 的 `uuid` 和 Codex 的复合键（`timestamp|type|payload_type|role|turn_id`）
- **_source 标记**：每条条目自动标记来源环境（`codex` / `claude`）
- **TypeError 防御**：压缩引擎已修复 list/dict/str 三种 output 类型的安全处理
- **压缩降级**：压缩异常时写兜底说明，原始 JSONL 不会丢失
