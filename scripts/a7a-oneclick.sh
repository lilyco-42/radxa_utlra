#!/usr/bin/env bash
# a7a-oneclick.sh —— A7A 一键入口（板子上执行）
#
# 把「体检 → 部署 → 验收」三件事收成一条命令，避免每次都要翻手册找脚本名。
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/lilyco-42/radxa_utlra/main/scripts/a7a-oneclick.sh | sudo bash -s -- --check
#
#   # 或者 clone 之后本地跑（推荐，能看清每一步）
#   git clone https://github.com/lilyco-42/radxa_utlra.git
#   cd radxa_utlra
#   sudo ./scripts/a7a-oneclick.sh --check      # 只体检，不改任何东西
#   sudo ./scripts/a7a-oneclick.sh --all        # 全量部署
#   sudo ./scripts/a7a-oneclick.sh --recovery   # 只恢复 VP 全链路
#
# 子命令：
#   --check      只体检（默认行为）
#   --all        全量：硬件能力 + VP 链路 + 定时器
#   --hardware   只装硬件能力（NPU/VE2/GPU/调频）
#   --recovery   只部署 VP 生成+发布链路
#   --verify     只做验收（检查已部署的东西是否真的能用）
#   --fix        系统层安全修复（mask 幽灵服务单元、切 CLI 目标等）
#   --fix-harden 上面的修复 + 写路径加固（会改变行为，慎用）
#
# 安全：全部幂等，可重复执行；--check 不修改任何文件。
#
# 系统层修复与部署的区别：
#   --hardware/--recovery 是「装能力」，--fix 是「修系统本身的问题」。
#   后者由 recovery/tools/board-fix.sh 实现，独立可用，也可以单独跑：
#     sudo ./recovery/tools/board-fix.sh --check

set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SELF_DIR/.." && pwd)"

MODE="check"
for a in "$@"; do
  case "$a" in
    --check)    MODE="check" ;;
    --all)      MODE="all" ;;
    --hardware) MODE="hardware" ;;
    --recovery) MODE="recovery" ;;
    --verify)   MODE="verify" ;;
    --fix)      MODE="fix" ;;
    --fix-harden) MODE="fix-harden" ;;
    -h|--help)  sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$a（--help 看用法）" >&2; exit 1 ;;
  esac
done

ok()   { printf '  \033[32m✅\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠️\033[0m  %s\n' "$*"; }
err()  { printf '  \033[31m❌\033[0m %s\n' "$*"; }
info() { printf '  ℹ️  %s\n' "$*"; }
log()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
die()  { err "$*"; exit 1; }

# 这个脚本是给**板子（Linux）**跑的。在 Windows 的 Git Bash / WSL 里误跑时，
# findmnt/free/uptime 不存在，会导致"存储健康度"一节误报成"读写挂载"。
# 所以先做一次环境判定，非 Linux 就直接拒绝，避免给出错误结论。
assert_linux_board() {
  case "$(uname -s)" in
    Linux) ;;
    *)
      err "本脚本需要在 A7A 板子上运行（当前系统：$(uname -s)）"
      info "Windows 上请用 scripts/flash-a7a.ps1 刷机"
      info "Linux/WSL 上刷机请用 scripts/flash-sd.sh"
      die "环境不匹配"
      ;;
  esac
  for c in findmnt free; do
    command -v "$c" >/dev/null 2>&1 || die "缺少 $c —— 这不像一块完整的 Linux 板子环境"
  done
  if [ "$(uname -m)" != "aarch64" ] && [ "$(uname -m)" != "arm64" ]; then
    warn "当前架构是 $(uname -m)，不是 aarch64 —— 你应该是在别的机器上跑的"
    warn "继续执行只会反映这台机器的状态，与 A7A 无关。"
  fi
}

run_step() {
  local title="$1"; shift
  log "$title"
  if "$@"; then ok "$title 完成"; else warn "$title 未成功（可能已安装或环境不满足，继续）"; fi
}

# ─────────────────────────────────────────────────────────────
# 体检
# ─────────────────────────────────────────────────────────────
do_check() {
  log "0) 系统基础信息"
  printf '  主机名:  %s\n' "$(hostname)"
  printf '  内核:    %s\n' "$(uname -r)"
  printf '  架构:    %s\n' "$(uname -m)"
  printf '  发行版:  %s\n' "$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
  printf '  内存:    %s\n' "$(free -m | awk 'NR==2{print $2" MB 总 / "$7" MB 可用"}')"
  printf '  磁盘 /:  %s\n' "$(df -h / | awk 'NR==2{print $2" 总 / "$4" 可用 ("$5" 已用)"}')"
  printf '  运行时长:%s\n' "$(uptime -p 2>/dev/null || uptime)"

  log "1) 基础依赖"
  for c in python3 node npm ffmpeg flock git; do
    if command -v "$c" >/dev/null 2>&1; then
      ok "$c  $(command -v $c)"
    else
      err "$c 缺失"
    fi
  done

  log "2) 硬件能力"
  # VE2 硬编码（注意：不是 V4L2，看 /dev/cedar*）
  for d in /dev/cedar_dev /dev/cedar_dev_ve2 /dev/dma_heap/system; do
    [ -e "$d" ] && ok "VE2 $d" || warn "VE2 $d 不存在（硬编不可用，会回退 libx264）"
  done
  [ -f /usr/lib/aarch64-linux-gnu/libvenc_h264.so ] && ok "libvenc_h264.so" || warn "libvenc_h264.so 缺失"

  # NPU
  if [ -e /dev/vipcore ] || [ -e /dev/galcore ]; then
    ok "NPU 设备节点存在（$(ls /dev/vipcore /dev/galcore 2>/dev/null | tr '\n' ' '))"
  else
    warn "NPU 设备节点不存在（当前 trixie 镜像可能未编 NPU 驱动）"
  fi

  # GPU
  if command -v vulkaninfo >/dev/null 2>&1; then
    local drv
    drv=$(vulkaninfo 2>/dev/null | grep -m1 -i 'driverName' | sed 's/.*= *//')
    [ -n "$drv" ] && ok "Vulkan: $drv" || warn "Vulkan 可用但读不到驱动名"
  else
    warn "vulkaninfo 未安装（跑 --hardware 会装）"
  fi

  log "3) 存储写路径健康度（这块板子的历史问题所在）"
  local rootfs_dev
  rootfs_dev=$(findmnt -nro SOURCE / | sed 's|/dev/||; s|p[0-9]*$||')
  printf '  根分区设备: %s\n' "$rootfs_dev"
  printf '  挂载选项:   %s\n' "$(findmnt -nro OPTIONS /)"
  if findmnt -nro OPTIONS / | grep -qE '(^|,)ro(,|$)'; then
    ok "根文件系统是只读挂载 —— 已受保护，不会被写坏"
  else
    warn "根文件系统是读写挂载"
    info "如果这块板子出现过「反复烧卡」，强烈建议改成只读："
    info "  sudo sed -i 's/ rw / ro /' /boot/extlinux/extlinux.conf"
  fi

  if [ -e /sys/block/mmcblk1/device/life_time ]; then
    ok "SD 卡寿命信息：life_time=$(cat /sys/block/mmcblk1/device/life_time 2>/dev/null)"
  fi
  # 内核里 MMC 相关错误（只看最近一次启动）
  local mmcerr
  mmcerr=$(dmesg 2>/dev/null | grep -ciE 'sunxi_mmc|smc [0-9] p[0-9]+ err|voltage select' || true)
  if [ "${mmcerr:-0}" -gt 0 ]; then
    warn "本次启动有 $mmcerr 条 MMC 控制器报错 —— 这是坏卡的前兆，看 docs/troubleshooting/a7a-sd-card-corruption.md"
  else
    ok "本次启动无 MMC 控制器报错"
  fi

  log "4) VP 链路状态"
  [ -d "$HOME/vp" ]              && ok "~/vp 存在"        || warn "~/vp 不存在（跑 --recovery）"
  [ -d "$HOME/vp/queue" ]        && ok "队列目录存在"      || warn "队列目录缺失"
  [ -d "$HOME/a733-cedarc" ]     && ok "a733-cedarc"      || warn "a733-cedarc 未装"
  [ -d "$HOME/html-video" ]      && ok "html-video"       || warn "html-video 未装"
  [ -d "$HOME/venv-tts" ]        && ok "venv-tts"         || warn "venv-tts 未建"
  [ -d "$HOME/biliup" ]          && ok "biliup"           || warn "biliup 未装"
  [ -d "$HOME/sau" ]             && ok "sau"              || warn "sau 未装"
  if systemctl --user is-active vp-pipeline.timer >/dev/null 2>&1; then
    ok "vp-pipeline.timer 运行中"
  else
    warn "vp-pipeline.timer 未运行"
  fi

  log "体检结论"
  info "绿=就绪，黄=缺组件（不影响体检本身）"
  info "想部署：sudo ./scripts/a7a-oneclick.sh --all"
}

# ─────────────────────────────────────────────────────────────
# 硬件能力
# ─────────────────────────────────────────────────────────────
do_hardware() {
  [ -f "$REPO_DIR/scripts/deploy-a7a-full-stack.sh" ] || die "找不到 deploy-a7a-full-stack.sh"
  run_step "全栈硬件能力部署" bash "$REPO_DIR/scripts/deploy-a7a-full-stack.sh"
}

# ─────────────────────────────────────────────────────────────
# VP 链路恢复
# ─────────────────────────────────────────────────────────────
do_recovery() {
  log "部署 VP 生成+发布链路"

  if [ -f "$REPO_DIR/recovery/deploy-recovery.sh" ]; then
    run_step "运行 recovery 部署脚本" bash "$REPO_DIR/recovery/deploy-recovery.sh"
  else
    # 内联兜底：仓库里没有单独脚本时，直接用恢复包里的部署器
    local deployer="$REPO_DIR/recovery/deploy-rebuild.sh"
    [ -f "$deployer" ] || die "找不到部署脚本（recovery/deploy-rebuild.sh）"
    run_step "运行 deploy-rebuild.sh" bash "$deployer"
  fi

  log "提示"
  info "配置模板：recovery/vp/config.example.yaml"
  info "  cp recovery/vp/config.example.yaml ~/vp/config.yaml && chmod 600 ~/vp/config.yaml"
  info "  # 然后填 llm.api_key 与平台账号"
  info "详细步骤见 docs/troubleshooting/a7a-rebuild-manual.md"
}

# ─────────────────────────────────────────────────────────────
# 系统层修复
# ─────────────────────────────────────────────────────────────
do_fix() {
  local extra=""
  [ "$MODE" = "fix-harden" ] && extra="--harden"

  log "系统层安全修复"
  local fixer="$REPO_DIR/recovery/tools/board-fix.sh"
  [ -f "$fixer" ] || die "找不到 $fixer"

  if [ "$MODE" = "fix-harden" ]; then
    warn "--harden 会改变系统行为（journal 转内存、fsck 频率等），请确认你了解代价"
  fi

  run_step "board-fix.sh --apply $extra" bash "$fixer" --apply $extra

  log "提示"
  info "体检报告（不改任何东西）：sudo $fixer --check"
  info "可修项清单：            $fixer --list"
  info "备份位置：              /root/board-fix-backup/<时间戳>/"
  info "完整解读：docs/troubleshooting/a7a-healthy-boot-log-analysis.md"
}

# ─────────────────────────────────────────────────────────────
# 验收
# ─────────────────────────────────────────────────────────────
do_verify() {
  local pass=0 fail=0
  chk() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$name"; pass=$((pass+1))
    else err "$name"; fail=$((fail+1)); fi
  }

  log "1) 基础"
  chk "python3" command -v python3
  chk "node"    command -v node
  chk "ffmpeg"  command -v ffmpeg
  chk "flock"   command -v flock

  log "2) VE2 硬编"
  chk "/dev/cedar_dev_ve2" test -e /dev/cedar_dev_ve2
  chk "/dev/dma_heap/system" test -e /dev/dma_heap/system
  chk "libvenc_h264.so" test -f /usr/lib/aarch64-linux-gnu/libvenc_h264.so

  log "3) VP 链路"
  chk "~/vp" test -d "$HOME/vp"
  chk "~/vp/queue/state.json" test -f "$HOME/vp/queue/state.json"
  chk "~/vp/run_pipeline.sh" test -f "$HOME/vp/run_pipeline.sh"
  chk "~/vp/process_queue.py" test -f "$HOME/vp/process_queue.py"
  chk "vp-pipeline.timer active" systemctl --user is-active vp-pipeline.timer

  log "4) 中文字体（渲染字幕必需）"
  if fc-list :lang=zh >/dev/null 2>&1 && [ -n "$(fc-list :lang=zh 2>/dev/null)" ]; then
    ok "中文字体已安装"
    pass=$((pass+1))
  else
    err "中文字体缺失：sudo apt install -y fonts-noto-cjk"
    fail=$((fail+1))
  fi

  log "5) 存储健康度"
  if dmesg 2>/dev/null | grep -qiE 'sunxi_mmc|smc [0-9] p[0-9]+ err'; then
    warn "有 MMC 报错，不计数"
  else
    ok "无 MMC 报错"
    pass=$((pass+1))
  fi

  printf '\n'
  printf '  通过 %d 项，失败 %d 项\n' "$pass" "$fail"
  [ "$fail" -eq 0 ] && ok "全部验收通过" || warn "有 $fail 项未通过，看上面红色项"
}

# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
printf '\n\033[1mA7A 一键部署\033[0m  (模式: %s)\n' "$MODE"
printf '─────────────────────────────────────────────────────────\n'

# 体检/验收两种"只读"模式允许在任意 Linux 上跑（方便先看环境），
# 但真正会改系统的模式必须是 A7A 板子。
case "$MODE" in
  check|verify)
    if [ "$(uname -s)" != "Linux" ]; then
      err "本脚本面向 Linux 板子；当前系统：$(uname -s)"
      info "Windows 刷机请用 scripts/flash-a7a.ps1"
      exit 1
    fi
    ;;
  *)
    assert_linux_board
    ;;
esac

case "$MODE" in
  check)      do_check ;;
  hardware)   do_check; do_hardware ;;
  recovery)   do_check; do_recovery ;;
  all)        do_check; do_hardware; do_recovery; do_fix; do_verify ;;
  verify)     do_verify ;;
  fix|fix-harden) do_check; do_fix ;;
esac

printf '\n'
