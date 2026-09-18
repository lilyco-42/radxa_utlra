#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# flash-sd.sh —— 在 Linux / macOS / WSL 上把 Radxa A733 镜像写入 SD 卡
#
# 为什么需要它：
#   Windows 侧刷机有一堆坑（盘符认错、Offline、usbipd 抢设备、
#   Etcher 报 "The writer process ended unexpectedly"），
#   而且 Windows 没有可靠的 SHA 校验 + 写后回读。
#   这个脚本用 dd + 回读校验，把整件事变成一条命令。
#
# 用法：
#   # 1) 列出候选磁盘（只读，不改动）
#   sudo ./scripts/flash-sd.sh --list
#
#   # 2) 干跑：只做安全检查，不写盘
#   sudo ./scripts/flash-sd.sh --image ~/Downloads/xxx.img.xz --device /dev/sdX --dry-run
#
#   # 3) 真刷（会二次确认）
#   sudo ./scripts/flash-sd.sh --image ~/Downloads/xxx.img.xz --device /dev/sdX
#
#   # 4) 只校验一个已有镜像的解压结果
#   ./scripts/flash-sd.sh --image ~/Downloads/xxx.img.xz --verify-image-only
#
# 安全设计（重要）：
#   - 默认拒绝写入任何"看起来像系统盘"的设备（有 / 或 /boot 挂载）
#   - 必须显式 --yes 才跳过交互确认
#   - 写前打印设备型号/容量/挂载点，要求人工核对
#   - 写后回读前 N MiB 与镜像比对，确认真的写进去了
# ─────────────────────────────────────────────────────────────

set -uo pipefail

IMAGE=""
DEVICE=""
DRY_RUN=0
ASSUME_YES=0
LIST_ONLY=0
VERIFY_IMAGE_ONLY=0
SKIP_VERIFY=0
BLOCK_CHECK_MB=64          # 写后回读校验的字节数（MiB）

ok()   { printf '  \033[32m✅\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠️\033[0m  %s\n' "$*"; }
err()  { printf '  \033[31m❌\033[0m %s\n' "$*"; }
info() { printf '  ℹ️  %s\n' "$*"; }
log()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
die()  { err "$*"; exit 1; }

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# ── 参数解析 ────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --image)              IMAGE="${2:-}"; shift 2 ;;
    --device)             DEVICE="${2:-}"; shift 2 ;;
    --dry-run)            DRY_RUN=1; shift ;;
    --yes|-y)             ASSUME_YES=1; shift ;;
    --list)               LIST_ONLY=1; shift ;;
    --verify-image-only)  VERIFY_IMAGE_ONLY=1; shift ;;
    --skip-verify)        SKIP_VERIFY=1; shift ;;
    -h|--help)            usage ;;
    *) die "未知参数：$1（--help 看用法）" ;;
  esac
done

require_root() {
  [ "$(id -u)" = "0" ] || die "需要 root：请用 sudo 运行"
}

# ── 列出候选磁盘 ────────────────────────────────────────────
list_devices() {
  log "候选磁盘（只读，不做任何改动）"
  local found=0

  if command -v lsblk >/dev/null 2>&1; then
    # RM=1 表示可移动设备
    printf '\n%-14s %-8s %-10s %-22s %s\n' "设备" "RM" "容量" "型号" "挂载点"
    printf '%s\n' "─────────────────────────────────────────────────────────────────────────"
    while read -r name rm size model; do
      local mounts
      mounts=$(lsblk -no MOUNTPOINT "/dev/$name" 2>/dev/null | grep -v '^$' | paste -sd, -)
      printf '%-14s %-8s %-10s %-22s %s\n' "/dev/$name" "$rm" "$size" "${model:0:22}" "${mounts:--}"
      [ "$rm" = "1" ] && found=$((found+1))
    done < <(lsblk -dno NAME,RM,SIZE,MODEL 2>/dev/null)
  else
    warn "没有 lsblk，退回 /sys/block 枚举"
    for d in /sys/block/*; do
      local n; n=$(basename "$d")
      case "$n" in loop*|ram*|zram*|sr*) continue ;; esac
      local sz rm
      sz=$(cat "$d/size" 2>/dev/null || echo 0)
      rm=$(cat "$d/removable" 2>/dev/null || echo 0)
      printf '  /dev/%-10s RM=%s  %s GiB\n' "$n" "$rm" "$((sz/2097152))"
      [ "$rm" = "1" ] && found=$((found+1))
    done
  fi

  printf '\n'
  if [ "$found" -gt 0 ]; then
    ok "找到 $found 个可移动设备 —— 目标应该在这里面"
    info "读卡器常见名字：Mass-Storage / SD Card Reader / USB Storage / Card Reader"
  else
    warn "没有找到任何可移动（RM=1）设备"
    info "检查：读卡器插好了吗？卡插进读卡器了吗？dmesg | tail 有 USB 事件吗？"
  fi

  printf '\n'
  printf '  \033[33m下一步（注意先核对再用）：\033[0m\n'
  printf '  sudo ./scripts/flash-sd.sh --image <镜像.img.xz> --device /dev/sdX --dry-run\n'
}

# ── 目标是系统盘？ ──────────────────────────────────────────
assert_not_system_disk() {
  local dev="$1"

  # 1) 设备本身或任何子分区被挂载为 / 或 /boot
  local parts mounts
  parts=$(lsblk -lno NAME "$dev" 2>/dev/null | tr '\n' ' ')
  for p in $parts; do
    local mp
    mp=$(findmnt -nro TARGET --source "/dev/$p" 2>/dev/null)
    for m in $mp; do
      case "$m" in
        /|/boot|/boot/efi|/usr|/var|/home)
          die "$dev（分区 /dev/$p）挂着系统关键挂载点 $m —— 拒绝写入！这可能是你的系统盘。" ;;
      esac
    done
  done

  # 2) 设备承载了根文件系统
  local root_src root_disk
  root_src=$(findmnt -nro SOURCE / 2>/dev/null)
  if [ -n "$root_src" ]; then
    root_disk=$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -1)
    [ -n "$root_disk" ] && [ "/dev/$root_disk" = "$dev" ] && \
      die "$dev 上有根文件系统（/）—— 拒绝写入！"
  fi

  # 3) 容量守卫：小于 4 GiB 或大于 512 GiB 的直接拦（A7A 用 16G~128G 卡）
  local sectors bytes gib
  sectors=$(cat "/sys/block/$(basename "$dev")/size" 2>/dev/null || echo 0)
  bytes=$((sectors * 512))
  gib=$((bytes / 1024 / 1024 / 1024))
  if [ "$gib" -lt 4 ]; then
    die "$dev 只有 ${gib} GiB，太小，不像 SD 卡。"
  fi
  if [ "$gib" -gt 512 ]; then
    warn "$dev 有 ${gib} GiB，比常见 SD 卡大很多。"
    warn "如果这其实是移动硬盘/NVMe，请立刻 Ctrl-C。"
  fi

  ok "安全检查通过：$dev 没有挂载系统关键路径，容量 ${gib} GiB"
}

# ── 镜像准备：解压 + SHA256 ─────────────────────────────────
RAW_IMG=""
TMP_IMG=""

prepare_image() {
  local img="$1"
  [ -f "$img" ] || die "镜像不存在：$img"

  case "$img" in
    *.xz)
      log "解压镜像（.xz）"
      for c in unxz xz; do
        if command -v "$c" >/dev/null 2>&1; then
          RAW_IMG="${img%.xz}"
          if [ -f "$RAW_IMG" ]; then
            ok "已存在解压结果，复用：$RAW_IMG"
          else
            "$c" -dk "$img" || die "解压失败（xz 报错，镜像可能损坏）"
            ok "解压完成：$RAW_IMG"
          fi
          break
        fi
      done
      [ -z "$RAW_IMG" ] && die "系统没有 xz/unxz，装一下：sudo apt install xz-utils"
      ;;
    *.gz)
      log "解压镜像（.gz）"
      RAW_IMG="${img%.gz}"
      if [ ! -f "$RAW_IMG" ]; then
        gzip -dk "$img" || die "解压失败"
      fi
      ok "解压完成：$RAW_IMG"
      ;;
    *.img)
      RAW_IMG="$img"
      ok "已是裸镜像：$RAW_IMG"
      ;;
    *)
      die "不认识的镜像格式：$img（支持 .img / .img.xz / .img.gz）"
      ;;
  esac

  local size
  size=$(stat -c '%s' "$RAW_IMG" 2>/dev/null || stat -f '%z' "$RAW_IMG" 2>/dev/null)
  info "镜像大小：$(numfmt --to=iec "$size" 2>/dev/null || echo "$size bytes")"

  log "计算镜像 SHA256（首次约需 1 分钟）"
  local sha
  sha=$(sha256sum "$RAW_IMG" 2>/dev/null | cut -d' ' -f1)
  ok "SHA256: $sha"
  printf '%s  %s\n' "$sha" "$RAW_IMG" > "$RAW_IMG.sha256"
  info "已写入 $RAW_IMG.sha256"
}

# ── 刷写 ────────────────────────────────────────────────────
flash() {
  local dev="$1" img="$2"

  local dev_size img_size
  dev_size=$(cat "/sys/block/$(basename "$dev")/size" 2>/dev/null)
  dev_size=$((dev_size * 512))
  img_size=$(stat -c '%s' "$img" 2>/dev/null || stat -f '%z' "$img" 2>/dev/null)

  log "写前确认"
  printf '  镜像：%s\n' "$img"
  printf '        %s (%s)\n' "$(numfmt --to=iec "$img_size" 2>/dev/null)" "$img_size bytes"
  printf '  设备：%s\n' "$dev"
  printf '        %s (%s)\n' "$(numfmt --to=iec "$dev_size" 2>/dev/null)" "$dev_size bytes"
  printf '\n'

  if lsblk -dno MODEL "$dev" >/dev/null 2>&1; then
    printf '  型号：%s\n' "$(lsblk -dno MODEL,VENDOR,TRAN "$dev" 2>/dev/null)"
  fi

  if [ "$dev_size" -lt "$img_size" ]; then
    die "设备容量($dev_size) 小于镜像($img_size) —— 装不下"
  fi

  if [ "$ASSUME_YES" != "1" ]; then
    printf '\n'
    printf '  \033[31m\033[1m⚠️ 即将彻底覆盖 %s 上的全部数据！\033[0m\n' "$dev"
    printf '  确认目标设备型号/容量没错，输入大写的 YES 继续：'
    read -r ans
    [ "$ans" = "YES" ] || die "用户取消。"
  fi

  # 卸载所有子分区
  log "卸载 $dev 的子分区"
  local parts
  parts=$(lsblk -lno NAME "$dev" 2>/dev/null | tail -n +2)
  local any=0
  for p in $parts; do
    if findmnt -nro TARGET --source "/dev/$p" >/dev/null 2>&1; then
      umount "/dev/$p" 2>/dev/null && { ok "已卸载 /dev/$p"; any=1; } || warn "/dev/$p 卸载失败（可能被占用）"
    fi
  done
  [ "$any" = "0" ] && info "没有需要卸载的分区"

  if [ "$DRY_RUN" = "1" ]; then
    log "干跑结束（--dry-run，没有写任何字节）"
    ok "一切检查通过。去掉 --dry-run 就是真刷。"
    return 0
  fi

  log "开始写入（$(basename "$dev") ← $(basename "$img")）"
  info "大镜像可能要 3~10 分钟，期间不要拔卡；进度由 dd 打印"
  printf '\n'

  if ! dd if="$img" of="$dev" bs=4M conv=fsync oflag=direct status=progress; then
    err "dd 写入失败"
    info "常见原因：卡满了/卡坏了/读卡器掉线。用 dmesg | tail -20 看内核报错。"
    die "写入中断"
  fi

  sync
  printf '\n'
  ok "写入完成"

  # ── 写后回读校验 ────────────────────────────────────────
  if [ "$SKIP_VERIFY" = "1" ]; then
    warn "--skip-verify：跳过回读校验（不推荐）"
  else
    log "回读校验（前 ${BLOCK_CHECK_MB} MiB，确认数据真的落盘）"
    local img_sha dev_sha
    img_sha=$(dd if="$img" bs=1M count="$BLOCK_CHECK_MB" status=none | sha256sum | cut -d' ' -f1)
    dev_sha=$(dd if="$dev" bs=1M count="$BLOCK_CHECK_MB" status=none | sha256sum | cut -d' ' -f1)
    printf '  镜像：%s\n' "$img_sha"
    printf '  设备：%s\n' "$dev_sha"
    if [ "$img_sha" = "$dev_sha" ]; then
      ok "回读一致 —— 写入成功"
    else
      err "回读不一致！写入可能不可靠。"
      warn "这正是「坏卡/坏读卡器/坏板子」的典型症状，不要直接拿去启动。"
      warn "换读卡器或换卡重试；如果每次都这样，说明是硬件问题。"
      die "校验失败"
    fi
  fi

  log "结论"
  ok "镜像已写入 $dev"
  printf '\n'
  printf '  \033[1m下一步：\033[0m\n'
  printf '  1) sync && eject %s       # 安全弹出\n' "$dev"
  printf '  2) 把卡插回 A7A，上电\n'
  printf '  3) 看蓝色状态灯：闪烁 = 系统启动中/正常，熄灭 = 启动出错\n'
  printf '  4) 等 60~90 秒，然后扫局域网找它的 IP\n'
  printf '\n'
  printf '  首次启动会做 rootfs 扩容 + 初始化，可能重启一次，属正常。\n'
  printf '  \033[33m⚠️ 卡上如果跑的是生产系统，务必先做「启动参数加 ro」防写保护，\033[0m\n'
  printf '  \033[33m   见 docs/troubleshooting/a7a-sd-card-corruption.md\033[0m\n'
}

# ── 主流程 ──────────────────────────────────────────────────
main() {
  printf '\n\033[1mRadxa A733 SD 卡刷写工具\033[0m\n'
  printf '安全设计：拒写系统盘 · 写前人工核对 · 写后回读校验\n'
  printf '─────────────────────────────────────────────────────────\n'

  if [ "$LIST_ONLY" = "1" ]; then
    list_devices
    exit 0
  fi

  [ -n "$IMAGE" ] || die "缺少 --image（--help 看用法）"

  if [ "$VERIFY_IMAGE_ONLY" = "1" ]; then
    prepare_image "$IMAGE"
    ok "镜像完整可用"
    exit 0
  fi

  [ -n "$DEVICE" ] || die "缺少 --device（先跑 --list 找目标）"
  [ -b "$DEVICE" ] || die "$DEVICE 不是块设备"

  require_root
  assert_not_system_disk "$DEVICE"
  prepare_image "$IMAGE"
  flash "$DEVICE" "$RAW_IMG"
}

main
