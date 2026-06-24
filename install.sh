#!/bin/bash
# Context Logger — 一键安装脚本
# 用法:
#   bash install.sh claude    # 安装到 Claude Code
#   bash install.sh codex     # 安装到 Codex Desktop
#   bash install.sh           # 交互式选择

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=============================="
echo " Context Logger 安装脚本"
echo "=============================="

# 检测目标
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    echo ""
    echo "请选择安装目标:"
    echo "  1) Claude Code"
    echo "  2) Codex Desktop"
    read -p "输入 1 或 2: " choice
    case "$choice" in
        1) TARGET="claude" ;;
        2) TARGET="codex" ;;
        *) echo "无效选择"; exit 1 ;;
    esac
fi

install_claude() {
    echo ""
    echo "📦 安装到 Claude Code..."

    SKILL_DIR="$HOME/.claude/skills/context-logger"
    mkdir -p "$SKILL_DIR/scripts"

    cp "$SCRIPT_DIR/SKILL.md" "$SKILL_DIR/SKILL.md"
    cp "$SCRIPT_DIR/scripts/transcript_manager.py" "$SKILL_DIR/scripts/transcript_manager.py"
    chmod +x "$SKILL_DIR/scripts/transcript_manager.py"

    echo ""
    echo "✅ 安装完成！"
    echo "   位置: $SKILL_DIR"
    echo ""
    echo "   ▶ 下次在 Claude Code 中说「记录上下文」即可使用"
    echo "   ▶ 或直接运行: python3 \"$SKILL_DIR/scripts/transcript_manager.py\" status"
}

install_codex() {
    echo ""
    echo "📦 安装到 Codex Desktop..."

    SCRIPTS_DIR="$HOME/.codex/scripts"
    mkdir -p "$SCRIPTS_DIR"

    cp "$SCRIPT_DIR/scripts/transcript_manager.py" "$SCRIPTS_DIR/transcript_manager.py"
    chmod +x "$SCRIPTS_DIR/transcript_manager.py"

    echo ""
    echo "✅ 安装完成！"
    echo "   位置: $SCRIPTS_DIR/transcript_manager.py"
    echo ""
    echo "   ▶ 下次在 Codex 中说「记录上下文」即可使用（需在 AGENTS.md 中注册）"
    echo "   ▶ 或直接运行: python3 \"$SCRIPTS_DIR/transcript_manager.py\" status"
}

case "$TARGET" in
    claude|Claude|CLAUDE)
        install_claude
        ;;
    codex|Codex|CODEX)
        install_codex
        ;;
    *)
        echo "❌ 未知目标: $TARGET"
        echo "   用法: bash install.sh [claude|codex]"
        exit 1
        ;;
esac
