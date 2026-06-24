---
name: context-logger
description: 自动保存 Claude Code / Codex 对话上下文到项目 transcripts 目录。同时产出原始 JSONL + 压缩 Markdown 精要。基于双格式 Entry ID 去重，永不重复。
---

# Context Logger

一键保存当前 AI 对话会话的**新增内容**到项目 `project_context/transcripts/` 目录。

**同时产出两类文件**：原始 JSONL + 压缩 Markdown 精要。

## 特点

- ✅ **双环境兼容**：同时支持 Claude Code 和 Codex Desktop，自动检测来源
- ✅ **增量永不重复**：基于 Entry ID 去重（兼容 Claude uuid 与 Codex 复合键）
- ✅ **来源自动标记**：每条条目注入 `_source` 字段（`codex` / `claude`）
- ✅ **TypeError 防御**：压缩引擎安全处理 list/dict/str 三种 output 类型，不崩
- ✅ **压缩失败不丢数据**：压缩异常时写兜底说明，原始 JSONL 完整保留

## 快速安装

### Claude Code 用户

```bash
# 一键安装
bash install.sh claude

# 手动测试
python3 scripts/transcript_manager.py status
```

### Codex Desktop 用户

```bash
# 一键安装
bash install.sh codex

# 手动测试
python3 scripts/transcript_manager.py status
```

## 使用方法

### 保存上下文（日常使用）

```bash
# 自动确定项目目录
python3 scripts/transcript_manager.py save

# 指定项目根目录（如果自动定位失败）
python3 scripts/transcript_manager.py save --project-root /path/to/project
```

### 查看存档状态

```bash
python3 scripts/transcript_manager.py status
```

### 合并历史档案（谨慎使用）

```bash
python3 scripts/transcript_manager.py merge
```

> ⚠️ `merge` 命令会将旧碎片文件移入 `raw/_archive/`，从 raw/ 目录看就像"被清空"。日常存档只用 `save`。

### 手动压缩 JSONL

```bash
python3 scripts/transcript_manager.py compress <input.jsonl> [output.md]
```

## 工作原理

1. 自动检测当前环境（Codex 或 Claude Code）
2. 读取当前 AI 会话的 JSONL 日志文件
3. 对比已存档的 Entry ID 集合，仅保存新增条目
4. 注入 `_source` 环境标记
5. 写入增量 JSONL + 同步生成压缩 Markdown

## 产物结构

```
project_context/transcripts/
├── .transcript_state              ← 存档状态文件
├── raw/                           ← 原始 JSONL 存档
│   ├── transcript_<时间>_incremental.jsonl  ← 增量存档
│   ├── transcript_<时间>_merged.jsonl       ← 合并后全量
│   └── _archive/                          ← 旧文件归档
└── compressed/                    ← 压缩 Markdown 精要
    └── transcript_<时间>_incremental_compressed.md
```

## 去重机制

两种格式的去重 ID 自动兼容：

| 环境 | 去重键 |
|------|--------|
| Claude Code | `uuid` 字段（标准 UUID v4） |
| Codex | `timestamp\|type\|payload_type\|role\|turn_id` 复合键 |

两种格式的存档可以共存于同一目录，去重互不冲突。

## 输出示例

```
✅ 增量保存完成
   新增: 42 条 (共 42 行)
   来源: claude
   JSONL: transcript_2026-06-24_0958_incremental.jsonl (982.4 KB)
   MD:    transcript_2026-06-24_0958_incremental_compressed.md (45.1 KB)
   累计存档: 7294 ID
```

## 许可证

MIT
