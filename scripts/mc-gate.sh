#!/usr/bin/env bash
# mc-gate.sh — 视频管道的资源协调闸门 (配合 video_tool 的 gate_command 使用)
#
# 退出码约定 (与 video_tool.watch._gate_open 一致):
#   0  = 放行, 允许视频处理
#   非0 = 暂停, 本轮不处理视频
#
# 策略: 把 CPU 让给正在玩 MC 的人
#   - MC 没在跑 / 连不上        -> 放行 (没人需要让路, 尽管跑)
#   - MC 在线 0 人              -> 放行
#   - MC 在线 > 0 人            -> 暂停视频
set -u

PING="${MC_PING:-/home/radxa/mc/bin/mc_ping.py}"
HOST="${MC_GATE_HOST:-127.0.0.1}"
PORT="${MC_GATE_PORT:-25565}"

# 连不上 (服务没起) 按"无人"处理 -> 放行, 别把流水线卡死
if ! count=$("$PING" "$HOST" "$PORT" 2>/dev/null); then
    exit 0
fi

# 解析不出整数也放行
case "$count" in
    ''|*[!0-9]*) exit 0 ;;
esac

if [ "$count" -gt 0 ]; then
    exit 1   # 有玩家在线 -> 暂停视频, 让出 CPU
fi
exit 0
