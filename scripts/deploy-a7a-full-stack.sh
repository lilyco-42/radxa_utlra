#!/usr/bin/env bash
#
# 一键部署 A7A 全套能力：NPU (galcore) + VE2 硬件编码 + GPU (Vulkan/OpenCL) + 性能调优
# 适用：Radxa Cubie A7A (Allwinner A733/sun60iw2)，Debian 12/13，内核 6.6.x
#
# 用法：
#   sudo ./deploy-a7a-full-stack.sh              # 全装
#   sudo ./deploy-a7a-full-stack.sh --npu        # 只装 NPU
#   sudo ./deploy-a7a-full-stack.sh --ve2        # 只装 VE2
#   sudo ./deploy-a7a-full-stack.sh --gpu        # 只检查/补装 GPU (Vulkan + OpenCL)
#   sudo ./deploy-a7a-full-stack.sh --perf       # 只做性能调优
#   sudo ./deploy-a7a-full-stack.sh --check      # 只检查现状，不改动
#
# 幂等：可重复执行，已装部分会跳过。
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
PATCH_DIR="$REPO_ROOT/patches"

DO_NPU=0
DO_VE2=0
DO_GPU=0
DO_PERF=0
CHECK_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --npu)   DO_NPU=1 ;;
        --ve2)   DO_VE2=1 ;;
        --gpu)   DO_GPU=1 ;;
        --perf)  DO_PERF=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) sed -n '3,18p' "$0"; exit 0 ;;
        *) echo "未知参数: $arg（-h 看帮助）" >&2; exit 1 ;;
    esac
done

# 没指定就全做
if [[ $DO_NPU -eq 0 && $DO_VE2 -eq 0 && $DO_GPU -eq 0 && $DO_PERF -eq 0 ]]; then
    DO_NPU=1; DO_VE2=1; DO_GPU=1; DO_PERF=1
fi

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

needs_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "需要 root：sudo $0 $*"
}

# ---------------------------------------------------------------- 环境检查
probe_environment() {
    log "环境检查"

    local board=""
    if [[ -r /proc/device-tree/model ]]; then
        board="$(tr -d '\0' < /proc/device-tree/model)"
    fi
    echo "  板子    : ${board:-未知}"
    echo "  内核    : $(uname -r)"
    echo "  架构    : $(uname -m)"
    echo "  发行版  : $(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-未知}")"

    case "$(uname -m)" in
        aarch64) ok "架构 aarch64" ;;
        *) die "本脚本只支持 aarch64，当前是 $(uname -m)" ;;
    esac

    if grep -qiE 'a733|a7a|cubie' <<<"${board}$(cat /proc/device-tree/compatible 2>/dev/null | tr -d '\0')"; then
        ok "识别为 Allwinner A733 平台"
    else
        warn "未识别出 A733，继续但可能失败"
    fi

    local kv; kv="$(uname -r)"
    if [[ "$kv" == 6.6.* ]]; then
        ok "内核 6.6.x（本方案已验证的目标版本）"
    elif [[ "$kv" == 5.15.* ]]; then
        warn "内核 5.15.x：vendor 驱动原生支持，本脚本的 NPU 补丁可能不适用"
    else
        warn "内核 $kv 未经验证（已验证：6.6.98-4-aw2511）"
    fi
}

# ---------------------------------------------------------------- NPU
check_npu() {
    log "NPU 现状"
    if grep -qw galcore /proc/modules; then
        ok "galcore 模块已加载"
    else
        warn "galcore 未加载"
    fi
    [[ -c /dev/galcore ]] && ok "/dev/galcore 存在" || warn "/dev/galcore 不存在"

    if [[ -e /sys/bus/platform/drivers/galcore/3600000.npu ]]; then
        ok "NPU 设备已绑定 galcore"
    else
        warn "NPU 设备未绑定 galcore"
    fi

    systemctl is-enabled npu-galcore.service >/dev/null 2>&1 \
        && ok "npu-galcore.service 已启用" \
        || warn "npu-galcore.service 未启用"

    local irq
    irq="$(grep -c 'galcore:0' /proc/interrupts 2>/dev/null || echo 0)"
    [[ "$irq" -gt 0 ]] && ok "NPU 中断线已注册" || warn "未见 NPU 中断线"
}

install_npu() {
    needs_root "$@"
    log "安装 NPU 栈（galcore 驱动 @ 6.6 内核）"

    local STACK="${A733_STACK_DIR:-/home/${SUDO_USER:-radxa}/a733-llama-npu-stack}"
    local STACK_USER="${SUDO_USER:-radxa}"

    # 1) 驱动源码
    if [[ ! -d "$STACK/.git" ]]; then
        log "克隆 reef1994/a733-llama-npu-stack"
        apt-get update -qq
        apt-get install -y -qq git build-essential "linux-headers-$(uname -r)" >/dev/null
        sudo -u "$STACK_USER" git clone --depth 1 \
            https://github.com/reef1994/a733-llama-npu-stack.git "$STACK"
    else
        ok "栈目录已存在：$STACK"
    fi

    # 2) 打 6.6 移植补丁（幂等：已打过则跳过）
    local PATCH="$PATCH_DIR/galcore-6.6-port.patch"
    [[ -f "$PATCH" ]] || die "找不到补丁：$PATCH"

    if sudo -u "$STACK_USER" git -C "$STACK" apply --reverse --check "$PATCH" 2>/dev/null; then
        ok "6.6 移植补丁已应用过，跳过"
    else
        log "应用 6.6 移植补丁（vm_flags/pin_user_pages/class_create/dma_buf 等 9 处 API 变更）"
        sudo -u "$STACK_USER" git -C "$STACK" apply "$PATCH" \
            || die "补丁应用失败，请检查内核版本是否匹配（补丁针对 6.6.x）"
        ok "补丁已应用"
    fi

    # 3) 构建驱动
    log "编译 galcore.ko（针对 $(uname -r)）"
    if [[ -f "$STACK/dist/driver/galcore.ko" ]] \
        && modinfo "$STACK/dist/driver/galcore.ko" 2>/dev/null | grep -q "$(uname -r)"; then
        ok "galcore.ko 已存在且匹配当前内核"
    else
        ( cd "$STACK" && make driver ) \
            || die "驱动编译失败，日志见上方输出"
        ok "galcore.ko 编译完成"
    fi

    # 4) 装 systemd 持久化单元
    log "配置开机自动加载（npu-galcore.service）"
    cat > /etc/systemd/system/npu-galcore.service <<EOF
[Unit]
Description=Load galcore NPU driver and bind Allwinner A733 NPU
After=systemd-modules-load.service
Before=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'grep -qw galcore /proc/modules || insmod $STACK/dist/driver/galcore.ko; if [ ! -e /sys/bus/platform/drivers/galcore/3600000.npu ]; then printf "%s\\n" 3600000.npu > /sys/bus/platform/drivers/vipcore/unbind 2>/dev/null; printf "%s\\n" 3600000.npu > /sys/bus/platform/drivers/galcore/bind; fi; [ -c /dev/galcore ] && chmod 666 /dev/galcore; true'

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now npu-galcore.service >/dev/null 2>&1 || true
    ok "npu-galcore.service 已启用并启动"

    check_npu
    warn "注意：开机后首次推理可能触发一次 galcore 自恢复（约 30s），属已知一次性现象"
}

# ---------------------------------------------------------------- VE2
check_ve2() {
    log "VE2 现状"
    for d in /dev/cedar_dev /dev/cedar_dev_ve2 /dev/dma_heap/system; do
        [[ -e "$d" ]] && ok "$d 存在" || warn "$d 缺失"
    done
    grep -qw sunxi_ve /proc/modules \
        && ok "sunxi_ve 模块已加载" \
        || warn "sunxi_ve 未加载"
    for lib in libVE.so libvencoder.so libvenc_h264.so; do
        [[ -e "/usr/lib/aarch64-linux-gnu/$lib" ]] \
            && ok "$lib" || warn "$lib 未安装"
    done
}

install_ve2() {
    needs_root "$@"
    log "安装 VE2 硬件 H.264 编码器"

    local USER_HOME="/home/${SUDO_USER:-radxa}"
    local D="$USER_HOME/a733-cedarc"

    # 1) 拿仓库
    if [[ ! -d "$D" ]]; then
        log "克隆 mashiqi/A733-Cedarc"
        apt-get install -y -qq git ffmpeg >/dev/null
        sudo -u "${SUDO_USER:-radxa}" git clone --depth 1 \
            https://github.com/mashiqi/A733-Cedarc.git "$D"
    else
        ok "仓库已存在：$D"
    fi

    # 2) 装厂商运行时库（8 个 .so + cedarc.conf + udev 规则）
    if [[ -e /usr/lib/aarch64-linux-gnu/libvenc_h264.so ]]; then
        ok "厂商运行时库已安装"
    else
        log "安装厂商运行时库"
        chmod +x "$D/scripts/"*.sh "$D/aw-h264-encoder" "$D/aw-h264-to-mp4" 2>/dev/null || true
        ( cd "$D" && ./scripts/install-runtime.sh ) || die "运行时安装失败"
        ok "运行时已安装（备份在 /var/backups/a733-cedarc/）"
    fi

    # 3) 应用我们的 MP4 封装修复（时长/退出码 bug）
    local FIX="$PATCH_DIR/aw-h264-to-mp4.fixed"
    if [[ -f "$FIX" && -f "$D/aw-h264-to-mp4" ]]; then
        if cmp -s "$FIX" "$D/aw-h264-to-mp4"; then
            ok "封装修复已就位"
        else
            log "应用 MP4 封装修复（时间戳两遍法重写）"
            cp -a "$D/aw-h264-to-mp4" "$D/aw-h264-to-mp4.bak" 2>/dev/null || true
            install -m 755 "$FIX" "$D/aw-h264-to-mp4"
            ok "修复已应用（原版备份 aw-h264-to-mp4.bak）"
        fi
    elif [[ ! -f "$FIX" ]]; then
        warn "未找到 $FIX，沿用原版（输出视频时长可能不准）"
    fi

    # 4) 便捷封装
    log "安装便捷命令 h264-ve2"
    install -m 755 "$SCRIPT_DIR/h264-ve2" /usr/local/bin/h264-ve2 2>/dev/null \
        || install -m 755 "$REPO_ROOT/scripts/h264-ve2" /usr/local/bin/h264-ve2
    ok "已安装到 /usr/local/bin/h264-ve2"

    # 5) 权限
    usermod -aG video "${SUDO_USER:-radxa}" 2>/dev/null || true

    check_ve2
}

# ---------------------------------------------------------------- GPU
check_gpu() {
    log "GPU 现状 (PowerVR BXM-4-64)"
    if [[ -x "$SCRIPT_DIR/gpu-check.sh" ]]; then
        bash "$SCRIPT_DIR/gpu-check.sh" --quick
    else
        grep -qw pvrsrvkm /proc/modules && ok "pvrsrvkm 已加载" || warn "pvrsrvkm 未加载"
        [[ -e /usr/lib/libVK_IMG.so ]] && ok "Vulkan 厂商库已装" || warn "libVK_IMG.so 缺失"
        [[ -f /etc/OpenCL/vendors/powervr.icd ]] && ok "OpenCL ICD 已注册" || warn "powervr.icd 缺失"
    fi
}

install_gpu() {
    needs_root "$@"
    log "GPU 栈（Vulkan + OpenCL）"

    # A7A 的 GPU 驱动随 Radxa 官方镜像预装（包 xserver-xorg-img-bxm），
    # 正常情况下无需安装，只做完整性补齐与验证。
    local need_pkgs=()
    dpkg -l vulkan-tools >/dev/null 2>&1 || need_pkgs+=(vulkan-tools)
    dpkg -l clinfo       >/dev/null 2>&1 || need_pkgs+=(clinfo)
    dpkg -l opencl-headers >/dev/null 2>&1 || need_pkgs+=(opencl-headers)
    dpkg -l g++          >/dev/null 2>&1 || need_pkgs+=(g++)

    if [[ ${#need_pkgs[@]} -gt 0 ]]; then
        log "安装验证工具：${need_pkgs[*]}"
        apt-get install -y -qq "${need_pkgs[@]}" >/dev/null || warn "部分包安装失败（不影响 GPU 本身）"
    else
        ok "验证工具齐全"
    fi

    # 确保用户在 video 组，能直接访问渲染节点
    local u="${SUDO_USER:-radxa}"
    if id -nG "$u" 2>/dev/null | grep -qw video; then
        ok "$u 已在 video 组"
    else
        usermod -aG video "$u" 2>/dev/null && ok "已把 $u 加入 video 组（需重新登录生效）" || true
    fi

    # 完整性校验 + 真实计算测试
    if [[ -x "$SCRIPT_DIR/gpu-check.sh" ]]; then
        echo
        log "运行 GPU 完整验证"
        bash "$SCRIPT_DIR/gpu-check.sh" || warn "GPU 验证未全部通过，见上方明细"
    else
        warn "未找到 gpu-check.sh，跳过验证"
    fi
}

# ---------------------------------------------------------------- 性能
check_perf() {
    log "性能调优现状"
    local gov
    gov="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo 未知)"
    echo "  CPU 调频: $gov"
    [[ "$gov" == "schedutil" ]] && ok "已用 schedutil" || warn "建议改为 schedutil（当前 $gov）"

    systemctl is-enabled cpu-governor.service >/dev/null 2>&1 \
        && ok "cpu-governor.service 已启用" \
        || warn "cpu-governor.service 未启用"
}

install_perf() {
    needs_root "$@"
    log "性能调优"

    # CPU 调频 → schedutil 并持久化
    local available
    available="$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors 2>/dev/null || echo '')"
    if grep -qw schedutil <<<"$available"; then
        cat > /etc/systemd/system/cpu-governor.service <<'EOF'
[Unit]
Description=Set CPU governor to schedutil on all cores
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'for c in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do echo schedutil > "$c" 2>/dev/null || true; done'

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable --now cpu-governor.service >/dev/null 2>&1 || true
        ok "schedutil 调频已启用并持久化"
    else
        warn "内核未提供 schedutil，跳过（当前可用：$available）"
    fi

    check_perf
}

# ---------------------------------------------------------------- 主流程
main() {
    echo
    echo "════════════════════════════════════════════════════════"
    echo "  Radxa A7A 全套能力一键部署"
    echo "  NPU (galcore) + VE2 硬编 + GPU (Vulkan/OpenCL) + 性能调优"
    echo "════════════════════════════════════════════════════════"
    echo

    probe_environment

    if [[ $CHECK_ONLY -eq 1 ]]; then
        echo
        [[ $DO_NPU -eq 1 ]]  && check_npu
        [[ $DO_VE2 -eq 1 ]]  && check_ve2
        [[ $DO_GPU -eq 1 ]]  && check_gpu
        [[ $DO_PERF -eq 1 ]] && check_perf
        echo
        ok "仅检查模式，未做任何改动"
        exit 0
    fi

    [[ $DO_NPU -eq 1 ]]  && { echo; install_npu "$@"; }
    [[ $DO_VE2 -eq 1 ]]  && { echo; install_ve2 "$@"; }
    [[ $DO_GPU -eq 1 ]]  && { echo; install_gpu "$@"; }
    [[ $DO_PERF -eq 1 ]] && { echo; install_perf "$@"; }

    echo
    echo "════════════════════════════════════════════════════════"
    ok "部署完成"
    echo
    echo "  验证 NPU :  ~/bin/a733-llama --list-devices"
    echo "              （应显示 A733: Allwinner A733 VIP9000Nano-DI via TIM-VX）"
    echo "  验证 VE2 :  h264-ve2 输入.mp4 输出.mp4"
    echo "  验证 GPU :  bash scripts/gpu-check.sh"
    echo "              （Vulkan 应显示 PowerVR B-Series + Imagination 原厂驱动）"
    echo "  重启后   :  NPU 会自动加载（npu-galcore.service）"
    echo "════════════════════════════════════════════════════════"
    echo
}

main "$@"
