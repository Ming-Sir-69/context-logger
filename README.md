# Context Logger

AI 对话上下文自动保存工具 — 同时支持 **Claude Code** 和 **Codex Desktop**。

安装后，对 AI 说「记录上下文」，它会自动保存本次会话的新增对话到项目目录。

## 安装

```bash
git clone https://github.com/Ming-Sir-69/context-logger.git
cd context-logger
bash install.sh
```

脚本会自动检测本地已安装的 Claude Code 和 Codex Desktop，识别到什么装什么。如果都识别不到，会交互询问。

## 使用

安装后告诉 AI：「保存上下文」即可。

AI 会自动调用脚本，输出类似：

```
✅ 增量保存完成
   新增: 42 条
   来源: claude
   JSONL: transcript_..._incremental.jsonl (982 KB)
   MD:    transcript_..._incremental_compressed.md (45 KB)
   累计存档: 7294 ID
```

## 产物

```
<项目>/project_context/transcripts/
├── raw/                    ← 原始对话 JSONL
└── compressed/             ← AI 可读的压缩摘要
```

## 卸载

```bash
# Claude Code
rm -rf ~/.claude/skills/context-logger

# Codex Desktop
rm ~/.codex/scripts/transcript_manager.py
```

## 许可证

MIT © 2026 Eric Mingle (Ming-Sir-69)
