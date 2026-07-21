#!/usr/bin/env bash
# Context Logger 安装脚本
# 自动安装到当前目录并注册 Claude Code Session 锚点

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXEC="${PYTHON_EXEC:-python3}"
MANAGER="$SCRIPT_DIR/scripts/transcript_manager.py"
HOOK_SCRIPT="$HOME/.claude/hooks/context-logger-session-anchor.sh"

# 检查 Python 可执行文件
if ! command -v "$PYTHON_EXEC" &>/dev/null; then
    echo "错误: 未找到 $PYTHON_EXEC" >&2
    exit 1
fi

# 检查 transcript_manager.py 是否存在
if [[ ! -f "$MANAGER" ]]; then
    echo "错误: 未找到 $MANAGER" >&2
    exit 1
fi

# 运行安装器
"$PYTHON_EXEC" "$SCRIPT_DIR/scripts/install_claude_hook.py" \
    --script "$MANAGER" \
    --hook-script "$HOOK_SCRIPT"

echo "安装完成！"
echo "Hook 脚本: $HOOK_SCRIPT"
echo "Context Logger 已注册到 Claude Code SessionStart"
