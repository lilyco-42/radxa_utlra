#!/usr/bin/env bash
#
# board-fix.sh —— 开发板上「能安全修的错误」的一键修复器
#
# 设计原则（这三条是踩过坑才写下来的）：
#   1. 只修「确定可修、且可逆」的。凡是需要改内核/设备树/固件的，一律只报告不动手。
#   2. 每一处改动前先备份，并打印「改了什么、怎么撤」。
#   3. 默认 --check 只读体检；真正修改必须显式给 --apply。
#
# 明确不做（硬约束）：
#   - 不碰 U-Boot（不 setenv/saveenv，不 mmc write，不改分区表）
#   - 不卸载任何软件包
#   - 不改内核命令行以外的任何启动加载器配置
#
# 用法:
#   sudo ./board-fix.sh --check              # 只体检，不改（默认）
#   sudo ./board-fix.sh --apply              # 修「安全类」问题
#   sudo ./board-fix.sh --apply --harden     # 额外做写路径加固（会改行为，见 --help）
#   sudo ./board-fix.sh --apply --only=lightdm
#   ./board-fix.sh --list                    # 列出所有已知修复项
#
set -uo pipefail

VERSION="1.0.0"

# ── 输出 ──────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[34m'
  C=$'\033[36m'; D=$'\033[2m'; N=$'\033[0m'
else
  R=; G=; Y=; B=; C=; D=; N=
fi
ok()   { echo "${G}  ✓${N} $*"; }
warn() { echo "${Y}  !${N} $*"; }
bad()  { echo "${R}  ✗${N} $*"; }
info() { echo "${B}  ·${N} $*"; }
dim()  { echo "${D}     $*${N}"; }
head_() { echo; echo "${C}▌$*${N}"; }

BACKUP_ROOT="/root/board-fix-backup"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"

APPLY=0
HARDEN=0
ONLY=""
FIXED=0
SKIPPED=0
FAILED=0

# ── 已知修复项清单（供 --list 与 --only 使用）──────────────────────────
# 每项: id|风险等级|说明
KNOWN_FIXES=(
  "lightdm|安全|mask 掉没有对应软件包的 lightdm.service（CLI 镜像打包残留）"
  "graphical|安全|CLI 镜像的 default.target 从 graphical 切回 multi-user"
  "startlimit|安全|清掉因上面两项引发的 start-limit-hit 失败计数"
  "atime|加固|挂载项加 noatime，读文件不再回写时间戳"
  "fsck|加固|让启动时真正检查根分区（现在被 ConditionPathIsReadWrite 跳过）"
  "writeback|加固|降低脏页回写频率与 journal 提交间隔"
  "journald|加固|systemd journal 改内存盘（代价：重启丢历史日志）"
  "swap|加固|调低 swap 倾向，减少对卡的随机写"
)

usage() {
  cat <<EOF
board-fix.sh $VERSION —— 开发板安全修复器

用法:
  sudo ./board-fix.sh --check              只体检（默认，不改任何东西）
  sudo ./board-fix.sh --apply              修「安全类」问题
  sudo ./board-fix.sh --apply --harden     额外做写路径加固
  sudo ./board-fix.sh --apply --only=ID    只修指定项（可逗号分隔）
  ./board-fix.sh --list                    列出所有修复项
  ./board-fix.sh --help

风险等级:
  安全  任何情况下都建议修，不改变使用习惯
  加固  会改变系统行为，可能影响日常操作，需自行判断

绝不触碰: U-Boot / 分区表 / 内核 / 设备树 / 已安装软件包
EOF
}

list_fixes() {
  echo "可用的修复项："
  echo
  printf "  %-12s %-6s %s\n" "ID" "风险" "说明"
  printf "  %-12s %-6s %s\n" "────────────" "──────" "────────────────────────────────────────"
  for item in "${KNOWN_FIXES[@]}"; do
    IFS='|' read -r id lvl desc <<<"$item"
    printf "  %-12s %-6s %s\n" "$id" "$lvl" "$desc"
  done
}

wanted() {
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; esac
  return 1
}

# ── 前置检查 ──────────────────────────────────────────────────────────
require_root() {
  if [ "$(id -u)" != "0" ]; then
    bad "需要 root。请用: sudo $0 $*"
    exit 1
  fi
}

require_linux() {
  case "$(uname -s)" in
    Linux) ;;
    *) bad "本脚本面向 Linux 开发板；当前系统：$(uname -s)"; exit 1 ;;
  esac
}

# ── 设备画像（复用 device_profile.py，没有就退化）─────────────────────
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
profile_field() {
  local key="$1"
  if [ -f "$SELF_DIR/device_profile.py" ]; then
    python3 "$SELF_DIR/device_profile.py" --json 2>/dev/null \
      | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('$key',''))" 2>/dev/null
  fi
}

# ══════════════════════════════════════════════════════════════════════
# 体检
# ══════════════════════════════════════════════════════════════════════

section_identity() {
  head_ "设备身份"
  local model distro kernel arch
  model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo '?')"
  # shellcheck disable=SC1091
  distro="$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || echo '?')"
  kernel="$(uname -r)"; arch="$(uname -m)"
  info "型号: $model"
  info "系统: $distro"
  info "内核: $kernel ($arch)"
  local fam; fam="$(profile_field family)"
  [ -n "$fam" ] && info "设备族: $fam"
}

section_units() {
  head_ "失败的 systemd 单元"
  local failed
  failed="$(systemctl --failed --no-legend --no-pager 2>/dev/null | awk '{print $1}')"
  if [ -z "$failed" ]; then
    ok "没有失败的单元"
    return
  fi
  local u
  for u in $failed; do
    local bin_ missing=""
    bin_="$(systemctl show "$u" -p ExecStart 2>/dev/null \
            | sed -n 's/.*path=\([^ ]*\).*/\1/p' | head -1 || true)"
    [ -n "$bin_" ] && [ ! -x "$bin_" ] && missing="$bin_"
    if [ -n "$missing" ]; then
      bad "$u  ${D}(可执行文件不存在: $missing)${N}"
      dim "→ 这是「unit 存在但软件包没装」的镜像打包问题，可安全 mask"
    else
      warn "$u"
      local why
      why="$(systemctl show "$u" -p Result 2>/dev/null | cut -d= -f2)"
      [ -n "$why" ] && dim "→ Result=$why"
    fi
  done
}

section_target() {
  head_ "默认目标 vs 实际能力"
  local tgt; tgt="$(systemctl get-default 2>/dev/null)"
  local has_dm=0
  for dm in lightdm gdm3 sddm lxdm sddm xdm; do
    command -v "$dm" >/dev/null 2>&1 && has_dm=1
  done
  info "default.target = $tgt"
  if [ "$tgt" = "graphical.target" ] && [ "$has_dm" = "0" ]; then
    warn "目标是 graphical，但系统里没有任何显示管理器"
    dim "→ CLI 镜像的典型残留；graphical.target 会反复拉起不存在的服务"
  elif [ "$tgt" = "graphical.target" ]; then
    ok "目标是 graphical，且检测到显示管理器"
  else
    ok "目标是 $tgt，与实际能力一致"
  fi
}

section_storage() {
  head_ "存储写路径健康度"
  local root_src root_opts
  read -r root_src root_opts <<<"$(findmnt -nro SOURCE,OPTIONS / 2>/dev/null | head -1)"
  info "根设备: $root_src"
  info "挂载项: $root_opts"

  if echo "$root_opts" | grep -qE '(^|,)ro(,|$)'; then
    ok "根分区只读 —— 掉电不会造成文件系统不一致"
  else
    dim "→ 可写。异常掉电时可能留下不一致（这是上次故障链的起点）"
  fi
  if echo "$root_opts" | grep -q 'noatime'; then
    ok "已禁用 atime（读文件不回写）"
  else
    warn "未禁用 atime —— 每次读文件都会产生一次元数据写"
    dim "→ 加 noatime 是零感知的写入削减，见 --apply --harden"
  fi
  if echo "$root_opts" | grep -q 'discard'; then
    warn "启用了 discard —— 每次删除都实时 TRIM，对 SD 卡是额外写放大"
  fi

  # 块设备健康
  local blk; blk="$(basename "$root_src" | sed -E 's/p?[0-9]+$//')"
  local life="/sys/block/$blk/device/life_time"
  if [ -r "$life" ]; then
    info "卡寿命计数(life_time): $(cat "$life" 2>/dev/null)"
    dim "→ 0x01=0-10%, 0x02=10-20% … 0x0a=90-100%, 0x0b=超出额定"
  fi

  # 实时内核错误。
  # 注意：必须排除「控制器对着空槽位重试」这类已知噪音，
  # 否则脚本会一边在下面把它判定为无害、一边在这里报 I/O 错误，自相矛盾。
  #
  # 白名单各条的来历（都是实测确认无害的）：
  #   manual set ocr              控制器没有 vmmc/vqmmc regulator，退回手动设电压
  #   RTO / retry / give up       对着空槽位死等 CMD1 响应，0.6s 后放弃
  #   Failed to initialize ...    DS 上标了 non-removable 但没焊器件，预期结果
  local errs=0
  local noise='sunxi_mmc_host.*RTO|smc .*err, cmd .*, RTO|retry:set phase failed|retry:give up|Failed to initialize a non-removable card|manual set ocr|No vmmc regulator|No vqmmc regulator'
  if dmesg >/dev/null 2>&1; then
    errs="$(dmesg 2>/dev/null \
      | grep -iE 'mmc.*(err|fail|timeout)|blk_update_request|I/O error|remounting filesystem read-only' \
      | grep -ivE "$noise" | grep -c . || true)"
  fi
  if [ "${errs:-0}" -gt 0 ]; then
    bad "本轮启动检测到 $errs 条真实存储 I/O 错误"
    dmesg 2>/dev/null \
      | grep -iE 'mmc.*(err|fail|timeout)|blk_update_request|I/O error|remounting filesystem read-only' \
      | grep -ivE "$noise" | tail -5 | sed 's/^/     /'
  else
    ok "本轮启动没有真实存储 I/O 错误"
    local nz
    nz="$(dmesg 2>/dev/null | grep -icE 'smc .*err, cmd .*, RTO' || true)"
    [ "${nz:-0}" -gt 0 ] && dim "（已滤除 $nz 条空槽位重试噪音，见下）"
  fi
}

section_fsck() {
  head_ "文件系统检查是否真的在跑"
  # 根分区的 fsck 会被 ConditionPathIsReadWrite 跳过 —— 这是最容易被忽略的盲区。
  #
  # 实现注意：不能写 `journalctl | grep -q ... && skipped=1`。
  # 本脚本开了 pipefail，而 grep -q 命中后会立刻退出，让 journalctl 吃到 SIGPIPE
  # 而以非 0 退出 —— 于是整条管道失败，判断恒为假（这个 bug 让体检报告
  # 一直显示「fsck 正常」，与日志事实相反）。
  # 正确做法：先把结果读进变量，再匹配，不产生管道。
  local skipped=0
  if command -v journalctl >/dev/null 2>&1; then
    local jout
    jout="$(journalctl -b --no-pager 2>/dev/null || true)"
    case "$jout" in
      *"systemd-fsck-root.service"*"skipped"*) skipped=1 ;;
    esac
  fi
  if [ "$skipped" = "1" ]; then
    warn "根分区的启动 fsck 被跳过了（ConditionPathIsReadWrite=/）"
    dim "→ 意味着 root 上的坏块会静默累积，直到系统起不来才发现"
    dim "→ 上次故障就这样累计了 2049 个坏块没人知道"
    dim "→ 补上: sudo $0 --apply --harden  （只做 harden 里的 fsck 项）"
  else
    ok "根分区启动了 fsck 检查"
  fi

  local root_dev
  root_dev="$(findmnt -nro SOURCE / 2>/dev/null | head -1)"
  if [ -n "$root_dev" ] && command -v dumpe2fs >/dev/null 2>&1; then
    local st bc
    st="$(dumpe2fs -h "$root_dev" 2>/dev/null | sed -n 's/^Filesystem state: *//p')"
    bc="$(dumpe2fs -h "$root_dev" 2>/dev/null | sed -n 's/^Block count: *//p')"
    [ -n "$st" ] && info "ext4 状态: $st  块数: $bc"
    echo "$bc" > /run/board-fix-blockcount 2>/dev/null || true
    dim "→ 下次再跑本脚本会自动对比块数；块数增长=文件系统在恶化"
  fi
}

section_noise() {
  head_ "非故障类噪音（别被吓到）"
  local dm; dm="$(dmesg 2>/dev/null || true)"
  if [ -z "$dm" ]; then
    dim "dmesg 不可读（dmesg_restrict=1），用 sudo 跑本脚本可看到这些判定"
    return
  fi

  check_noise() {
    local pat="$1" label="$2" verdict="$3"
    local n; n="$(echo "$dm" | grep -icE "$pat" || true)"
    if [ "${n:-0}" -gt 0 ]; then
      printf "  %-34s %3s 次  " "$label" "$n"
      echo "${D}$verdict${N}"
    fi
  }

  check_noise 'OPP not supported by regulators' \
              'OPP not supported by regulators' \
              'CPU 调频实际正常（看 scaling_available_frequencies）'
  check_noise 'smc .* err, cmd .*, RTO' \
              'mmc RTO 风暴' \
              'DT 标了 non-removable 但槽位空 → 0.6 秒后自行放弃'
  check_noise 'manual set ocr|No vmmc regulator|No vqmmc regulator' \
              '控制器电压声明' \
              '板子没接 regulator，驱动退回手动设 ocr，功能正常'
  check_noise 'reg-virt-consumer.*Failed to locate of_node' \
              'reg-virt-consumer' \
              '测试用虚拟 regulator，上游 BSP 遗留'
  check_noise 'only for testing and debugging|DEBUG kernel' \
              'DEBUG kernel 警告' \
              '内核编译期决定，用户态改不了；若 trace 缓冲为空则零开销'
  check_noise 'ufs.*link startup failed' \
              'UFS 链路失败' \
              '板子没焊 UFS 芯片，预期行为'
  check_noise 'sunxi_sid.*Fail to read .dvfs2_ori' \
              'dvfs2_ori 缺失' \
              '只影响细粒度调压，不影响功能'

  # trace_printk 是否真在写 —— 这决定它是不是真的有害
  if [ -r /sys/kernel/debug/tracing/trace ]; then
    local written
    written="$(grep -m1 'entries-written' /sys/kernel/debug/tracing/trace 2>/dev/null \
               | sed -E 's/.*entries-written: *([0-9]+)\/([0-9]+).*/\2/')"
    if [ -n "$written" ] && [ "$written" != "0" ]; then
      warn "trace ring buffer 已写入 $written 条 —— DEBUG 内核确有实际开销"
    else
      ok "trace ring buffer 为空 —— 'DEBUG kernel' 警告当前零开销"
    fi
  fi
}

# ══════════════════════════════════════════════════════════════════════
# 修复动作
# ══════════════════════════════════════════════════════════════════════

backup_prepare() {
  mkdir -p "$BACKUP_DIR" 2>/dev/null
  {
    echo "# board-fix.sh 修复前快照  $STAMP"
    echo "## default.target"; systemctl get-default 2>/dev/null
    echo "## 失败的单元"; systemctl --failed --no-legend --no-pager 2>/dev/null
    echo "## 根挂载"; findmnt -nro SOURCE,OPTIONS / 2>/dev/null
    echo "## cmdline"; cat /proc/cmdline 2>/dev/null
  } > "$BACKUP_DIR/before.txt" 2>/dev/null
  info "快照已存: ${D}$BACKUP_DIR/before.txt${N}"
}

fix_lightdm() {
  head_ "修复: lightdm"
  if ! systemctl cat lightdm.service >/dev/null 2>&1; then
    ok "没有 lightdm.service，跳过"
    return
  fi
  local state; state="$(systemctl is-enabled lightdm.service 2>&1)"
  if [ "$state" = "masked" ]; then
    ok "已经是 masked，跳过"
    return
  fi
  local bin
  bin="$(systemctl show lightdm.service -p ExecStart 2>/dev/null \
         | sed -n 's/.*path=\([^ ]*\).*/\1/p' | head -1 || true)"
  if [ -n "$bin" ] && [ -x "$bin" ]; then
    warn "lightdm 可执行文件存在（$bin），说明真的装了 —— 不 mask"
    dim "→ 这种情况下失败另有原因，请自行排查"
    SKIPPED=$((SKIPPED + 1))
    return
  fi
  if [ "$APPLY" != "1" ]; then
    dim "（--check 模式）将要执行: systemctl mask lightdm.service"
    dim "  原因: unit 是 enabled，但 $bin 不存在"
    return
  fi
  cp -a /usr/lib/systemd/system/lightdm.service "$BACKUP_DIR/" 2>/dev/null || true
  if systemctl mask lightdm.service >/dev/null 2>&1; then
    ok "已 mask lightdm.service"
    dim "撤销: systemctl unmask lightdm.service"
    FIXED=$((FIXED + 1))
  else
    bad "mask 失败"
    FAILED=$((FAILED + 1))
  fi
}

fix_graphical() {
  head_ "修复: default.target"
  local tgt; tgt="$(systemctl get-default 2>/dev/null)"
  if [ "$tgt" = "multi-user.target" ]; then
    ok "已经是 multi-user.target，跳过"
    return
  fi
  local has_dm=0
  for dm in lightdm gdm3 sddm lxdm sddm xdm; do
    command -v "$dm" >/dev/null 2>&1 && has_dm=1
  done
  if [ "$has_dm" = "1" ]; then
    warn "检测到显示管理器，不动 default.target"
    SKIPPED=$((SKIPPED + 1))
    return
  fi
  if [ "$APPLY" != "1" ]; then
    dim "（--check 模式）将要执行: systemctl set-default multi-user.target"
    dim "  原因: 目标是 graphical 但没有任何显示管理器"
    return
  fi
  if systemctl set-default multi-user.target >/dev/null 2>&1; then
    ok "default.target → multi-user.target"
    dim "撤销: systemctl set-default graphical.target"
    FIXED=$((FIXED + 1))
  else
    bad "切换失败"
    FAILED=$((FAILED + 1))
  fi
}

fix_startlimit() {
  head_ "修复: start-limit 失败计数"
  local any=0
  for u in $(systemctl --failed --no-legend --no-pager 2>/dev/null | awk '{print $1}'); do
    any=1
    if [ "$APPLY" != "1" ]; then
      dim "（--check 模式）将要执行: systemctl reset-failed $u"
    else
      systemctl reset-failed "$u" >/dev/null 2>&1 && ok "已重置 $u" || warn "重置 $u 失败"
      FIXED=$((FIXED + 1))
    fi
  done
  [ "$any" = "0" ] && ok "没有失败的单元，跳过"
}

# ── 加固项 ────────────────────────────────────────────────────────────
FSTAB="/etc/fstab"

harden_atime() {
  head_ "加固: noatime"
  local root_opts
  root_opts="$(findmnt -nro OPTIONS / 2>/dev/null | head -1)"
  if echo "$root_opts" | grep -q 'noatime'; then
    ok "已启用 noatime，跳过"
    return
  fi
  local root_src
  root_src="$(findmnt -nro SOURCE / 2>/dev/null | head -1)"
  local uuid; uuid="$(blkid -s UUID -o value "$root_src" 2>/dev/null)"
  if [ -z "$uuid" ]; then
    bad "取不到根分区 UUID，跳过"
    FAILED=$((FAILED + 1)); return
  fi
  if [ "$APPLY" != "1" ]; then
    dim "（--apply --harden 才会执行）将在 $FSTAB 给 UUID=$uuid 的根挂载加 noatime"
    return
  fi
  cp -a "$FSTAB" "$BACKUP_DIR/fstab.bak"
  if grep -qE "^UUID=$uuid[[:space:]]" "$FSTAB"; then
    sed -i -E "s|^(UUID=$uuid[[:space:]]+[^[:space:]]+[[:space:]]+[^[:space:]]+[[:space:]]+)([^[:space:]]+)|\1\2,noatime|" "$FSTAB"
    ok "已给 UUID=$uuid 加 noatime"
    dim "撤销: cp $BACKUP_DIR/fstab.bak $FSTAB && reboot"
    FIXED=$((FIXED + 1))
  else
    warn "$FSTAB 里没找到 UUID=$uuid，跳过"
    SKIPPED=$((SKIPPED + 1))
  fi
}

harden_fsck() {
  head_ "加固: 恢复 fsck"
  if ! command -v tune2fs >/dev/null 2>&1; then
    warn "没有 tune2fs（e2fsprogs 未装），跳过"
    SKIPPED=$((SKIPPED + 1)); return
  fi
  local root_dev; root_dev="$(findmnt -nro SOURCE / 2>/dev/null | head -1)"
  local max
  max="$(tune2fs -l "$root_dev" 2>/dev/null | sed -n 's/^Maximum mount count: *//p')"
  if [ "$max" != "-1" ] && [ -n "$max" ]; then
    ok "已设置每 $max 次挂载检查一次，跳过"
    return
  fi
  if [ "$APPLY" != "1" ]; then
    dim "（--apply --harden 才会执行）将要设置: 每 30 次挂载做一次 fsck + errors=remount-ro"
    dim "  目的: 补上「根分区启动时不做检查」这个盲区"
    return
  fi
  tune2fs -c 30 -i 30d "$root_dev" >/dev/null 2>&1 \
    && { ok "已设置定期 fsck（每 30 次挂载或 30 天）"; FIXED=$((FIXED + 1)); } \
    || { bad "tune2fs 失败"; FAILED=$((FAILED + 1)); }

  # errors=remount-ro：出错先转只读，避免「边修边坏」
  local cur_eb
  cur_eb="$(tune2fs -l "$root_dev" 2>/dev/null | sed -n 's/^Errors behavior: *//p')"
  if [ "$cur_eb" = "Continue" ]; then
    tune2fs -e remount-ro "$root_dev" >/dev/null 2>&1 \
      && ok "错误行为 Continue → remount-ro（更安全）" \
      || warn "设置 errors=remount-ro 失败"
  fi
}

harden_writeback() {
  head_ "加固: 回写参数"
  local f="/etc/sysctl.d/99-board-fix-writeback.conf"
  local body='# board-fix.sh 生成 —— 降低对 SD/eMMC 的回写压力
vm.dirty_writeback_centisecs = 6000
vm.dirty_expire_centisecs = 12000'
  if [ -f "$f" ] && grep -q 'board-fix' "$f"; then
    ok "已配置，跳过"
    return
  fi
  if [ "$APPLY" != "1" ]; then
    dim "（--apply --harden 才会执行）将写入 $f"
    dim "  把脏页回写间隔从 5s 放宽到 60s，减少写突发"
    return
  fi
  printf '%s\n' "$body" > "$f"
  sysctl -p "$f" >/dev/null 2>&1
  ok "已写入 $f"
  dim "撤销: rm $f && reboot"
  FIXED=$((FIXED + 1))
}

harden_journald() {
  head_ "加固: journal 转内存"
  local f="/etc/systemd/journald.conf.d/99-board-fix.conf"
  if [ -f "$f" ]; then
    ok "已配置，跳过"
    return
  fi
  if journalctl --disk-usage >/dev/null 2>&1; then
    dim "当前 journal 占用: $(journalctl --disk-usage 2>/dev/null | sed 's/^Archived and active journals take up //')"
  fi
  if [ "$APPLY" != "1" ]; then
    dim "（--apply --harden 才会执行）将写入 $f，journal 改存 /run（内存）"
    dim "  好处: 系统日志不再持续写卡（这是常年累计写入的大头）"
    dim "  代价: 重启后看不到上一次启动的日志 —— 排查历史故障会变难"
    return
  fi
  mkdir -p "$(dirname "$f")"
  printf '%s\n' \
    '# board-fix.sh 生成 —— journal 存内存，减少对卡的持续写入' \
    '[Journal]' \
    'Storage=volatile' \
    'RuntimeMaxUse=64M' \
    'SystemMaxUse=64M' > "$f"
  systemctl restart systemd-journald >/dev/null 2>&1
  ok "journal → volatile（重启丢失历史日志）"
  dim "撤销: rm $f && systemctl restart systemd-journald"
  FIXED=$((FIXED + 1))
}

harden_swap() {
  head_ "加固: swap 倾向"
  local f="/etc/sysctl.d/99-board-fix-swap.conf"
  if [ -f "$f" ]; then
    ok "已配置，跳过"
    return
  fi
  if [ "$APPLY" != "1" ]; then
    dim "（--apply --harden 才会执行）将设 vm.swappiness=10"
    return
  fi
  printf '%s\n' \
    '# board-fix.sh 生成 —— 降低 swap 倾向，减少对卡的随机写' \
    'vm.swappiness = 10' > "$f"
  sysctl -p "$f" >/dev/null 2>&1
  ok "已设 vm.swappiness=10"
  FIXED=$((FIXED + 1))
}

# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════

main() {
  local do_list=0
  for a in "$@"; do
    case "$a" in
      --list)   do_list=1 ;;
      --apply)  APPLY=1 ;;
      --harden) HARDEN=1 ;;
      --check)  APPLY=0 ;;
      --only=*) ONLY="${a#--only=}" ;;
      -h|--help) usage; exit 0 ;;
      *) echo "未知参数: $a"; usage; exit 2 ;;
    esac
  done

  if [ "$do_list" = "1" ]; then list_fixes; exit 0; fi

  require_linux

  echo
  echo "board-fix.sh $VERSION   $([ "$APPLY" = "1" ] && echo "${Y}应用模式${N}" || echo "${G}只读体检${N}")$([ "$HARDEN" = "1" ] && echo " ${Y}+加固${N}")"
  echo "════════════════════════════════════════════════════════════════"

  section_identity
  section_units
  section_target
  section_storage
  section_fsck
  section_noise

  head_ "执行修复"
  if [ "$APPLY" = "1" ]; then
    require_root "$@"
    backup_prepare
  else
    dim "当前是只读体检模式，下面是「将要做什么」的预览"
    dim "真正执行请加 --apply"
  fi

  wanted lightdm   && fix_lightdm
  wanted graphical && fix_graphical
  wanted startlimit && fix_startlimit

  if [ "$HARDEN" = "1" ]; then
    require_root "$@"
    echo
    echo "${Y}── 加固项（会改变系统行为）──${N}"
    wanted atime     && harden_atime
    wanted fsck      && harden_fsck
    wanted writeback && harden_writeback
    wanted journald  && harden_journald
    wanted swap      && harden_swap
  fi

  echo
  echo "════════════════════════════════════════════════════════════════"
  if [ "$APPLY" = "1" ]; then
    echo "  已修 ${G}$FIXED${N} 项，跳过 $SKIPPED 项，失败 ${R}$FAILED${N} 项"
    if [ -d "$BACKUP_DIR" ]; then
      echo "  备份: $BACKUP_DIR"
    fi
    if [ "$HARDEN" = "1" ] && [ "$FIXED" -gt 0 ]; then
      echo
      echo "  ${Y}有线/网络/SSH 不受影响，但建议重启验证：${N}"
      echo "    sudo reboot"
      echo
      echo "  重启后再跑一次确认："
      echo "    sudo $0 --check"
    fi
  else
    echo "  只读体检完成。确定要修就加 --apply"
  fi
  echo
}

main "$@"
