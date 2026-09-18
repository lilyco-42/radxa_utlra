#!/usr/bin/env bash
# deploy-recovery.sh — A7A「生成 + 发布」全链路重建
#
# 背景：2026-09-17~18 连续两张 SD 卡损坏，板上的 ~/vp、~/sau、~/biliup 等
#       全部丢失。本脚本用仓库里 recovery/vp 的内容把整条链路一键恢复。
#
# 用法（在板子上执行，仓库根目录内）:
#   ./recovery/deploy-recovery.sh --check     # 只体检，不改动
#   ./recovery/deploy-recovery.sh             # 执行重建
#
#   # 也可通过统一入口调用：
#   sudo ./scripts/a7a-oneclick.sh --recovery
#
# 覆盖：
#   ~/vp/                     队列 + 处理器 + 流水线脚本 + 模板 + 配置
#   ~/.config/systemd/user/   vp-pipeline.service/timer
#   ~/html-video/             渲染管线（含 Cedar 补丁，若源在则打补丁）
#   ~/a733-cedarc/            VE2 硬编码器（若未装则提示）
#
# 幂等：可重复执行。
# ⚠️ 不会覆盖已存在的 ~/vp/config.yaml（保护你已填的密钥）。
# ⚠️ 不碰 U-Boot、不碰分区表。
#
# 详细手册：docs/troubleshooting/a7a-rebuild-manual.md

set -uo pipefail

VP="$HOME/vp"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

ok()   { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }
err()  { echo "  ❌ $*"; }
log()  { echo; echo "== $* =="; }

# ── 体检模式 ──────────────────────────────────────────
if [ "$CHECK_ONLY" = "1" ]; then
  log "环境体检"
  echo "内核:      $(uname -r)"
  echo "内存:      $(free -m | awk 'NR==2{print $2"MB 总 / "$7"MB 可用"}')"
  echo "磁盘 /:    $(df -h / | awk 'NR==2{print $2" 总 / "$4" 可用"}')"
  echo "主机名:    $(hostname)"
  echo
  echo "依赖检查:"
  for c in python3 node ffmpeg flock; do
    if command -v "$c" >/dev/null 2>&1; then ok "$c"; else err "$c 缺失"; fi
  done
  echo "VE2 设备:"
  for d in /dev/cedar_dev /dev/cedar_dev_ve2 /dev/dma_heap/system; do
    if [ -e "$d" ]; then ok "$d"; else warn "$d 不存在"; fi
  done
  echo "可选组件:"
  [ -d "$HOME/a733-cedarc" ]   && ok "a733-cedarc"   || warn "a733-cedarc 未安装（Cedar 硬编码不可用，将用 libx264）"
  [ -d "$HOME/html-video" ]    && ok "html-video"    || warn "html-video 未安装（需先 clone）"
  [ -d "$HOME/venv-tts" ]      && ok "venv-tts"      || warn "venv-tts 未安装（需建虚拟环境）"
  echo
  echo "当前 VP 状态:"
  [ -d "$VP" ] && ok "$VP 存在" || warn "$VP 不存在（将新建）"
  systemctl --user is-active vp-pipeline.timer 2>/dev/null | grep -q active \
    && ok "timer 运行中" || warn "timer 未运行"
  exit 0
fi

log "1/6 创建目录结构"
mkdir -p "$VP/logs" "$VP/videos" "$VP/queue" "$HOME/bin"
ok "$VP/{logs,videos,queue}"

log "2/6 部署 VP 核心文件"
[ -f "$SELF_DIR/vp/run_pipeline.sh" ] && { install -m 755 "$SELF_DIR/vp/run_pipeline.sh" "$VP/run_pipeline.sh"; ok "run_pipeline.sh"; } || err "缺 run_pipeline.sh"
[ -f "$SELF_DIR/vp/process_queue.py" ] && { install -m 755 "$SELF_DIR/vp/process_queue.py" "$VP/process_queue.py"; ok "process_queue.py"; } || err "缺 process_queue.py"
[ -f "$SELF_DIR/vp/queue/state.json" ] && { install -m 644 "$SELF_DIR/vp/queue/state.json" "$VP/queue/state.json"; ok "queue/state.json"; } || err "缺 queue/state.json"

if [ -f "$SELF_DIR/vp/queue/lyco-rust-20260917.json" ]; then
  install -m 644 "$SELF_DIR/vp/queue/lyco-rust-20260917.json" "$VP/queue/lyco-rust-20260917.json"
  N=$(python3 -c "import json;print(len(json.load(open('$VP/queue/lyco-rust-20260917.json'))['tasks']))" 2>/dev/null || echo '?')
  ok "任务队列（$N 条）"
else
  err "缺任务队列 JSON"
fi

# 可选：如果本地带了 vp 的其他脚本，一并部署
for f in gen_script.py gen_video_srt.mjs gen_video.mjs publish.py pick_model.py fix_llm_key.py topics.txt; do
  if [ -f "$SELF_DIR/vp/$f" ]; then
    install -m 755 "$SELF_DIR/vp/$f" "$VP/$f"
    ok "$f"
  fi
done

# 渲染模板（要保留目录结构，不能只 install 文件）
if [ -d "$SELF_DIR/vp/templates" ]; then
  mkdir -p "$VP/templates"
  cp -r "$SELF_DIR/vp/templates/." "$VP/templates/"
  ok "templates/"
fi

# 配置：只从模板生成，绝不用模板覆盖用户已填好的真实配置
log "2.5/6 生成配置"
if [ -f "$VP/config.yaml" ]; then
  ok "~/vp/config.yaml 已存在，保留不动（不覆盖你的密钥）"
  chmod 600 "$VP/config.yaml" 2>/dev/null || true
elif [ -f "$SELF_DIR/vp/config.example.yaml" ]; then
  install -m 600 "$SELF_DIR/vp/config.example.yaml" "$VP/config.yaml"
  ok "已从模板生成 ~/vp/config.yaml（权限 600）"
  warn "还需填写：llm.api_key，以及各平台账号后把 enabled 改 true"
else
  err "缺 config.example.yaml"
fi

log "3/6 部署 systemd user 单元"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
for u in vp-pipeline.service vp-pipeline.timer; do
  if [ -f "$SELF_DIR/systemd/$u" ]; then
    install -m 644 "$SELF_DIR/systemd/$u" "$UNIT_DIR/$u"
    ok "$u"
  else
    err "缺 systemd/$u"
  fi
done

log "4/6 检查 html-video 渲染管线"
if [ -d "$HOME/html-video" ]; then
  ok "html-video 已存在"
  if [ -f "$HOME/html-video/packages/adapter-hyperframes/dist/render.js" ]; then
    if grep -q "aw-h264-to-mp4" "$HOME/html-video/packages/adapter-hyperframes/dist/render.js" 2>/dev/null; then
      ok "Cedar 补丁已在位"
    else
      warn "Cedar 补丁未应用（用 patch_cedar_v4.py 打，或保持 libx264）"
    fi
  fi
else
  warn "html-video 未安装 —— 需先 clone 并 npm install"
  echo "       git clone <html-video 仓库> ~/html-video && cd ~/html-video && npm install"
fi

log "5/6 检查 VE2 硬编码器"
if [ -d "$HOME/a733-cedarc" ]; then
  ok "a733-cedarc 已存在"
  [ -x "$HOME/a733-cedarc/aw-h264-to-mp4" ] && ok "aw-h264-to-mp4 可执行" || warn "aw-h264-to-mp4 不可执行"
else
  warn "a733-cedarc 未安装（Cedar 路径会跳过，自动回退 libx264）"
  echo "       git clone https://github.com/mashiqi/A733-Cedarc ~/a733-cedarc"
  echo "       然后 sudo ~/a733-cedarc/scripts/install-runtime.sh"
fi

log "6/6 启动定时器"
systemctl --user daemon-reload
systemctl --user enable vp-pipeline.timer >/dev/null 2>&1 && ok "timer enabled" || warn "enable 失败"
systemctl --user start vp-pipeline.timer >/dev/null 2>&1 && ok "timer started" || warn "start 失败"

echo
echo "== 完成 =="
echo "队列状态:"
python3 "$VP/process_queue.py" --status 2>/dev/null || warn "状态查看失败"
echo
echo "定时器:"
systemctl --user list-timers vp-pipeline.timer --no-pager 2>/dev/null | head -3
echo
echo "下一步:"
echo "  1) 手动跑一个任务验证:  python3 ~/vp/process_queue.py"
echo "  2) 打开发布需先登录各平台，再把 state.json 的 publish 改成 true"
