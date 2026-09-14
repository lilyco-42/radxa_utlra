#!/usr/bin/env bash
#
# A7A 路由器一键配置：PPPoE 拨号 + WiFi 热点 + NAT 转发
# 适用：Radxa Cubie A7A (Allwinner A733/sun60iw2)，Debian 12/13，内核 6.6.x
#
# 用法：
#   sudo ./deploy-router.sh --check                                  # 只体检，不改动
#   sudo ./deploy-router.sh --pppoe --user U --pass P --mac AA:BB:CC:DD:EE:FF
#   sudo ./deploy-router.sh --ap --ssid MyAP --ap-pass 12345678
#   sudo ./deploy-router.sh --nat                                    # NAT 转发
#   sudo ./deploy-router.sh --all --user U --pass P --mac AA:BB:CC:DD:EE:FF
#
# 可选：
#   --iface <name>     WAN 网口（默认 end0）
#   --ppp-if <name>    PPPoE 逻辑接口名（默认 ppp0）
#   --mac <MAC>        克隆到 WAN 口的 MAC（仅当线路绑定 MAC 时才需要，见 docs 第七节）
#   --mtu <n>          PPPoE MTU/MRU（默认 1480）
#   --channel <n>      热点信道（默认 6）
#   --tx-delay <n>     千兆网口 tx-delay（默认 9，改这个修发帧损坏）
#
# 幂等：可重复执行。详细说明见 docs/a7a-router-mode.md
#
set -euo pipefail

# ---------------------------------------------------------------- 默认值
IFACE="end0"            # WAN 物理网口
PPP_IF="ppp0"           # PPPoE 逻辑接口
WAN_MAC=""
MTU=1480
PPP_USER=""
PPP_PASS=""
AP_SSID="radxa-ap"
AP_PASS="12345678"
AP_CHANNEL=6
TX_DELAY=9

DO_PPPOE=0
DO_AP=0
DO_NAT=0
CHECK_ONLY=0

usage() { sed -n '3,22p' "$0"; exit 0; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)     CHECK_ONLY=1 ;;
        --pppoe)     DO_PPPOE=1 ;;
        --ap)        DO_AP=1 ;;
        --nat)       DO_NAT=1 ;;
        --all)       DO_PPPOE=1; DO_AP=1; DO_NAT=1 ;;
        --iface)     IFACE="${2:?--iface 需要参数}"; shift ;;
        --ppp-if)    PPP_IF="${2:?--ppp-if 需要参数}"; shift ;;
        --mac)       WAN_MAC="${2:?--mac 需要参数}"; shift ;;
        --mtu)       MTU="${2:?--mtu 需要参数}"; shift ;;
        --user)      PPP_USER="${2:?--user 需要参数}"; shift ;;
        --pass)      PPP_PASS="${2:?--pass 需要参数}"; shift ;;
        --ssid)      AP_SSID="${2:?--ssid 需要参数}"; shift ;;
        --ap-pass)   AP_PASS="${2:?--ap-pass 需要参数}"; shift ;;
        --channel)   AP_CHANNEL="${2:?--channel 需要参数}"; shift ;;
        --tx-delay)  TX_DELAY="${2:?--tx-delay 需要参数}"; shift ;;
        -h|--help)   usage ;;
        *) echo "未知参数: $1（-h 看帮助）" >&2; exit 1 ;;
    esac
    shift
done

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

needs_root() { [[ ${EUID:-$(id -u)} -eq 0 ]] || die "需要 root：sudo $0 $*"; }

# ---------------------------------------------------------------- 体检
probe_environment() {
    log "环境检查"

    local board=""
    [[ -r /proc/device-tree/model ]] && board="$(tr -d '\0' < /proc/device-tree/model)"
    echo "  板子    : ${board:-未知}"
    echo "  内核    : $(uname -r)"
    echo "  发行版  : $(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-未知}")"

    case "$(uname -m)" in
        aarch64) ok "架构 aarch64" ;;
        *) warn "非 aarch64（$(uname -m)），本脚本按 A7A 编写，可能不适用" ;;
    esac

    # WAN 网口
    if ip link show "$IFACE" >/dev/null 2>&1; then
        ok "WAN 网口 $IFACE 存在（MAC $(cat "/sys/class/net/$IFACE/address")）"
    else
        die "找不到网口 $IFACE（用 --iface 指定）"
    fi

    # tx-delay —— 不改会发帧损坏
    local td="/sys/class/net/$IFACE/device/tx_delay"
    if [[ -r $td ]]; then
        local cur; cur="$(cat "$td")"
        if [[ $cur == "$TX_DELAY" ]]; then
            ok "tx_delay = $cur"
        else
            warn "tx_delay = $cur（应为 $TX_DELAY）—— 否则发帧损坏，PPPoE 连发现阶段都过不去"
        fi
    else
        warn "读不到 $td（该网口可能无此参数）"
    fi

    # 内核模块
    local m
    for m in ppp_generic pppoe pppox; do
        if lsmod | grep -q "^$m"; then ok "模块 $m 已加载"
        else warn "模块 $m 未加载（modprobe pppoe 会带上）"; fi
    done

    # pppd
    if command -v pppd >/dev/null 2>&1; then
        ok "pppd 已装（$(command -v pppd)）"
    else
        warn "pppd 未装：apt install ppp"
    fi
    if ls /usr/lib/pppd/*/pppoe.so >/dev/null 2>&1; then
        ok "pppoe.so 插件存在"
    else
        warn "找不到 pppoe.so 插件（注意：不是 rp-pppoe.so）"
    fi

    # WiFi
    if [[ -d /sys/class/net/wlan0 ]]; then
        ok "WiFi wlan0 存在（MAC $(cat /sys/class/net/wlan0/address)）"
        if iw phy 2>/dev/null | grep -qE '^[[:space:]]+\* AP$'; then
            ok "驱动声明支持 AP 模式"
        else
            warn "驱动未声明 AP 模式"
        fi
    else
        warn "没有 wlan0"
    fi

    if command -v nmcli >/dev/null 2>&1; then ok "NetworkManager 可用（nmcli）"
    else warn "没有 nmcli，热点/NAT 需要它"; fi

    # 现状
    log "当前状态"
    if ip -4 addr show "$PPP_IF" 2>/dev/null | grep -q 'inet '; then
        echo "  $PPP_IF : $(ip -4 -br addr show "$PPP_IF" | awk '{print $3}')"
    else
        echo "  $PPP_IF : 未建立"
    fi
    ip -4 -br addr show wlan0 2>/dev/null | sed 's/^/  /' || true
    echo "  ip_forward = $(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo '?')"
}

# ---------------------------------------------------------------- tx-delay
fix_txdelay() {
    local td="/sys/class/net/$IFACE/device/tx_delay"
    [[ -r $td ]] || { warn "无 tx_delay 节点，跳过"; return 0; }

    local cur; cur="$(cat "$td")"
    if [[ $cur == "$TX_DELAY" ]]; then
        ok "tx_delay 已是 $TX_DELAY"
        return 0
    fi

    log "修 tx-delay：$cur → $TX_DELAY（不改会发帧损坏，PPPoE 不通）"
    echo "$TX_DELAY" > "$td"
    ok "tx_delay = $(cat "$td")"
    warn "这是运行时值，重启会丢（持久化见 docs/a7a-router-mode.md 第七节）"
}

# ---------------------------------------------------------------- PPPoE
do_pppoe() {
    needs_root
    [[ -n $PPP_USER && -n $PPP_PASS ]] || die "--pppoe 需要 --user 和 --pass"

    fix_txdelay

    log "加载内核模块"
    modprobe pppoe 2>/dev/null || die "modprobe pppoe 失败"
    ok "pppoe / ppp_generic / pppox 就绪"

    log "释放网口 $IFACE"
    if command -v nmcli >/dev/null 2>&1; then
        nmcli device set "$IFACE" managed no 2>/dev/null || true
        ok "NetworkManager 已松手"
    fi
    ip link set "$IFACE" up

    if [[ -n $WAN_MAC ]]; then
        log "克隆 MAC → $WAN_MAC"
        ip link set "$IFACE" address "$WAN_MAC"
        ok "$IFACE MAC = $(cat "/sys/class/net/$IFACE/address")"
    else
        warn "未指定 --mac；仅当这条线路绑定 MAC 时才需要（多数线路不需要，见 docs 第七节）"
    fi

    log "启动 pppd"
    pkill -f "pppd plugin pppoe.so $IFACE" 2>/dev/null || true
    sleep 1
    pppd plugin pppoe.so "$IFACE" \
         user "$PPP_USER" password "$PPP_PASS" \
         mtu "$MTU" mru "$MTU" noauth defaultroute usepeerdns \
         logfile /var/log/ppp.log &
    ok "pppd 已后台启动（日志 /var/log/ppp.log）"

    log "等待协商（最多 30s）"
    local i
    for i in $(seq 1 30); do
        if ip -4 addr show "$PPP_IF" 2>/dev/null | grep -q 'inet '; then
            ok "拨号成功"
            ip -4 -br addr show "$PPP_IF" | sed 's/^/  /'
            return 0
        fi
        sleep 1
    done

    err "30s 内未拿到 IP，日志尾部："
    tail -25 /var/log/ppp.log 2>/dev/null || true
    die "PPPoE 拨号失败"
}

# ---------------------------------------------------------------- WiFi AP
do_ap() {
    needs_root
    [[ -d /sys/class/net/wlan0 ]] || die "没有 wlan0"
    command -v nmcli >/dev/null 2>&1 || die "需要 NetworkManager（nmcli）"

    log "创建热点 $AP_SSID（信道 $AP_CHANNEL）"
    nmcli device wifi hotspot ifname wlan0 con-name radxa-ap \
         ssid "$AP_SSID" band bg channel "$AP_CHANNEL" password "$AP_PASS"
    sleep 5

    ok "wlan0 = $(ip -4 -br addr show wlan0 | awk '{print $3}')"
    iw dev wlan0 info 2>/dev/null | grep -E 'ssid|type|channel' | sed 's/^/  /' || true
    ok "dnsmasq 由 NM 自动拉起（10.42.0.10-254）"
    warn "NM 不会自动加 NAT —— 还要跑 --nat，否则客户端连得上但上不了网"
}

# ---------------------------------------------------------------- NAT
do_nat() {
    needs_root

    log "开 IP 转发"
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    ok "ip_forward = $(sysctl -n net.ipv4.ip_forward)"

    log "加 NAT 规则（10.42.0.0/24 → $PPP_IF）"
    if iptables -t nat -C POSTROUTING -s 10.42.0.0/24 -o "$PPP_IF" -j MASQUERADE 2>/dev/null; then
        ok "MASQUERADE 已存在"
    else
        iptables -t nat -A POSTROUTING -s 10.42.0.0/24 -o "$PPP_IF" -j MASQUERADE
        ok "已加 MASQUERADE"
    fi

    if iptables -C FORWARD -i wlan0 -o "$PPP_IF" -j ACCEPT 2>/dev/null; then
        ok "FORWARD(wlan0→$PPP_IF) 已存在"
    else
        iptables -A FORWARD -i wlan0 -o "$PPP_IF" -j ACCEPT
        ok "已加 FORWARD(wlan0→$PPP_IF)"
    fi

    if iptables -C FORWARD -i "$PPP_IF" -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
        ok "FORWARD(回程) 已存在"
    else
        iptables -A FORWARD -i "$PPP_IF" -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT
        ok "已加 FORWARD(回程)"
    fi

    warn "iptables 规则是运行时状态，重启会丢 —— 用 iptables-persistent 持久化"
}

# ---------------------------------------------------------------- main
main() {
    probe_environment

    if [[ $CHECK_ONLY -eq 1 ]]; then
        log "仅体检模式，未做任何改动"
        exit 0
    fi

    if [[ $DO_PPPOE -eq 0 && $DO_AP -eq 0 && $DO_NAT -eq 0 ]]; then
        usage
    fi

    if [[ $DO_PPPOE -eq 1 ]]; then do_pppoe; fi
    if [[ $DO_AP -eq 1 ]]; then do_ap; fi
    if [[ $DO_NAT -eq 1 ]]; then do_nat; fi

    log "完成"
    echo "  验证：拿另一台设备连上热点 $AP_SSID，然后执行"
    echo "    ip a                                        # 应拿到 10.42.0.x"
    echo "    ping 10.42.0.1                              # 网关"
    echo "    curl -s http://members.3322.org/dyndns/getip  # 出口 IP"
    echo "  出口 IP 应与板子自身 curl 出来的一致。"
}

main "$@"
