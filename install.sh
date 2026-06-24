#!/bin/bash
# Context Logger — 一键安装脚本
# 自动检测本地已安装的 Claude Code / Codex Desktop 环境，识别到什么装什么。
# 用法:
#   bash install.sh        # 自动检测并安装
#   bash install.sh --all  # 强制安装到两个环境（即使未检测到也创建目录）

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=============================="
echo " Context Logger 安装脚本"
echo "=============================="

detected=0
FORCE_ALL=false
if [ "$1" = "--all" ]; then
    FORCE_ALL=true
fi

install_claude() {
    echo ""
    echo "📦 安装到 Claude Code..."
    SKILL_DIR="$HOME/.claude/skills/context-logger"
    mkdir -p "$SKILL_DIR/scripts"
    cp "$SCRIPT_DIR/SKILL.md" "$SKILL_DIR/SKILL.md"
    cp "$SCRIPT_DIR/scripts/transcript_manager.py" "$SKILL_DIR/scripts/transcript_manager.py"
    chmod +x "$SKILL_DIR/scripts/transcript_manager.py"
    echo "   ✅ 位置: $SKILL_DIR"
}

install_codex() {
    echo ""
    echo "📦 安装到 Codex Desktop..."
    SCRIPTS_DIR="$HOME/.codex/scripts"
    mkdir -p "$SCRIPTS_DIR"
    cp "$SCRIPT_DIR/scripts/transcript_manager.py" "$SCRIPTS_DIR/transcript_manager.py"
    chmod +x "$SCRIPTS_DIR/transcript_manager.py"
    echo "   ✅ 位置: $SCRIPTS_DIR/transcript_manager.py"
}

# ── 自动检测 ────────────────────────────────────────

if [ -d "$HOME/.claude/skills" ]; then
    install_claude
    detected=$((detected + 1))
fi

if [ -d "$HOME/.codex/scripts" ] || [ -d "$HOME/.codex" ]; then
    install_codex
    detected=$((detected + 1))
fi

# ── 兜底 ────────────────────────────────────────────
if [ "$detected" -eq 0 ]; then
    echo ""
    echo "⚠️  未检测到 Claude Code 或 Codex Desktop 的目录。"
    echo ""
    echo "请选择安装目标:"
    echo "  1) Claude Code（~/.claude/skills/）"
    echo "  2) Codex Desktop（~/.codex/scripts/）"
    echo "  3) 两个都装"
    read -p "输入 1 / 2 / 3: " choice
    case "$choice" in
        1) install_claude ;;
        2) install_codex ;;
        3) install_claude; install_codex ;;
        *) echo "❌ 无效选择"; exit 1 ;;
    esac
fi

if [ "$FORCE_ALL" = true ]; then
    install_claude
    install_codex
fi

echo ""
echo "✅ 安装完成！"
echo ""
echo "   在 Claude Code 中说「记录上下文」即可使用。"
echo "   在 Codex 中同样说「记录上下文」即可使用。"
echo ""
echo "   手动执行: python3 scripts/transcript_manager.py status"
