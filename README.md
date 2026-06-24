# Context Logger

AI 对话上下文自动保存工具 — 同时支持 **Claude Code** 和 **Codex Desktop**。

一句话概括：在 Claude Code 或 Codex 中说一声「记录上下文」，脚本自动对比已存档内容，只保存新对话条目，同时产出原始 JSONL + 压缩 Markdown 精要。

## 快速开始

```bash
# 克隆到本地
git clone https://github.com/Ming-Sir-69/context-logger.git
cd context-logger

# 安装到 Claude Code
bash install.sh claude

# 或安装到 Codex Desktop
bash install.sh codex
```

## 功能特性

| 特性 | 说明 |
|------|------|
| **双环境兼容** | 自动检测 Codex 或 Claude Code，使用对应的去重键 |
| **增量永不重复** | Entry ID 去重（兼容 uuid 和复合键两种格式） |
| **来源标记** | 每条存档条目自动注入 `_source` 字段 |
| **TypeError 防御** | 压缩引擎安全处理 list/dict/str 三种 output 类型 |
| **压缩降级** | 压缩失败时写兜底说明，原始 JSONL 不丢 |

## 使用方法

```bash
# 保存当前上下文（日常使用）
python3 scripts/transcript_manager.py save

# 查看存档状态
python3 scripts/transcript_manager.py status

# 合并历史档案（谨慎！旧文件会被归档）
python3 scripts/transcript_manager.py merge

# 手动压缩 JSONL
python3 scripts/transcript_manager.py compress <input.jsonl> [output.md]
```

## 产物结构

```
<项目>/project_context/transcripts/
├── raw/                       ← 原始 JSONL
│   ├── transcript_..._incremental.jsonl
│   ├── transcript_..._merged.jsonl
│   └── _archive/              ← merge 时归档的旧文件
└── compressed/                ← 压缩摘要
    └── transcript_..._incremental_compressed.md
```

## CLI 集成

### Claude Code

安装后，在 Claude Code 中说「保存上下文」「记录上下文」「存档对话」等即可触发。

### Codex Desktop

安装后需在 `AGENTS.md` 中注册触发词。建议添加：

```bash
用户说「保存上下文」「记录上下文」→ 运行 python3 ~/.codex/scripts/transcript_manager.py save
```

## 许可证

MIT

## 作者

[Ming-Sir-69](https://github.com/Ming-Sir-69)
