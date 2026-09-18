#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lan_sweep.py —— 局域网设备发现与识别（跨平台）

比"只 ping 一遍"多做的事：
    1. ping 探活
    2. TCP 端口探测（SSH / HTTP / VNC 等）
    3. 从 banner / MAC 前缀识别设备身份（哪块板子、什么厂商）
    4. 明确标出"疑似 SBC 开发板"，而不只是列 IP

为什么需要：
    板子出问题时最常见的情况是"IP 变了 / 没起来 / 起来了但不响应 ping"。
    只 ping 不足以判断 —— 有的系统禁 ICMP 但 SSH 正常；有的活着但服务全挂。
    分端口探测能区分这几种状态。

用法：
    python lan_sweep.py                          # 自动猜网段
    python lan_sweep.py 192.168.10               # 指定 /24
    python lan_sweep.py 192.168.10 --ports 22,80,443
    python lan_sweep.py 192.168.10 --identify    # 深挖 banner 与 MAC 厂商
    python lan_sweep.py 192.168.10 --json        # 机器可读

跨平台：
    Windows 的 ping 输出是 GBK 中文，必须显式解码（这是踩过的坑）。
    Linux/macOS 的 ping 参数不同（-W 单位是秒），已分支处理。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import platform
import re
import socket
import subprocess
import sys
import time

IS_WINDOWS = platform.system() == "Windows"

# 常见端口 → 服务名
PORT_NAMES = {
    22: "SSH",
    23: "telnet",
    53: "DNS",
    80: "HTTP",
    111: "rpcbind",
    443: "HTTPS",
    1880: "NodeRED",
    3000: "HTTP-alt",
    5000: "HTTP-alt",
    5001: "iperf3",
    5900: "VNC",
    8080: "HTTP-alt",
    8443: "HTTPS-alt",
    9090: "cockpit",
}

# 默认探测端口：跟嵌入式开发板最相关的几个
DEFAULT_PORTS = [22, 80, 443, 8080, 9090]

# OUI（MAC 前缀）→ 厂商。只挑跟本场景相关的（完整 IEEE 库有 3 万条）。
OUI_VENDORS = {
    # 树莓派
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi",
    "e4:5f:01": "Raspberry Pi", "28:cd:c1": "Raspberry Pi",
    "d8:3a:dd": "Raspberry Pi", "2c:cf:67": "Raspberry Pi",
    # 虚拟化
    "52:54:00": "QEMU/KVM 虚拟机",
    "00:0c:29": "VMware", "00:50:56": "VMware",
    "00:1c:42": "Parallels",
    "08:00:27": "VirtualBox",
    # 常见板载 / USB 网卡芯片
    "00:e0:4c": "Realtek（常见 USB 网卡/板载）",
    "00:1b:21": "Intel", "00:1e:67": "Intel",
    "0b:da": "Realtek",
    "dc:fb:48": "UBIQUITI",
    "00:25:90": "Supermicro",
}

# 从 SSH banner 猜操作系统/发行版
SSH_BANNER_HINTS = [
    (r"Debian", "Debian"), (r"Ubuntu", "Ubuntu"),
    (r"Raspbian", "Raspbian"), (r"Armbian", "Armbian"),
    (r"dropbear", "dropbear（常见于嵌入式精简系统）"),
    (r"OpenSSH_9", "较新的 OpenSSH"),
]


# ══════════════════════════════════════════════════════════════════════
# 网段推断
# ══════════════════════════════════════════════════════════════════════

# 这些网段不可能是「你要找的开发板」所在的网段，必须排除：
#   127.x        回环
#   169.254.x    link-local（没拿到 DHCP）
#   198.18/15    RFC2544 基准测试段 —— Clash/mihomo 等代理的 TUN 口默认用它，
#                表现为「扫出 253 台全活、端口全开」，极具迷惑性
#   198.19.x     同上（198.18.0.0/15 覆盖 198.18 与 198.19）
#   100.64/10    CGNAT / Tailscale 常用
#   172.17.x     Docker 默认网桥
_BOGUS_SUBNET_PREFIXES = (
    "127.", "169.254.", "198.18.", "198.19.", "100.64.", "100.65.",
    "100.66.", "100.67.", "100.68.", "100.69.", "100.70.", "100.71.",
    "100.72.", "100.73.", "100.74.", "100.75.", "100.76.", "100.77.",
    "100.78.", "100.79.", "100.80.", "100.81.", "100.82.", "100.83.",
    "100.84.", "100.85.", "100.86.", "100.87.", "100.88.", "100.89.",
    "100.90.", "100.91.", "100.92.", "100.93.", "100.94.", "100.95.",
    "100.96.", "100.97.", "100.98.", "100.99.", "100.100.", "100.101.",
    "100.102.", "100.103.", "100.104.", "100.105.", "100.106.", "100.107.",
    "100.108.", "100.109.", "100.110.", "100.111.", "100.112.", "100.113.",
    "100.114.", "100.115.", "100.116.", "100.117.", "100.118.", "100.119.",
    "100.120.", "100.121.", "100.122.", "100.123.", "100.124.", "100.125.",
    "100.126.", "100.127.",
    "172.17.", "172.18.", "172.19.",
)

# 网卡名里含这些字样 → 是虚拟/隧道口，不要拿来猜局域网网段
_VIRTUAL_IF_HINTS = (
    "tun", "tap", "utun", "clash", "mihomo", "wg", "tailscale", "docker",
    "veth", "br-", "vmnet", "vboxnet", "hyper-v", "loopback", "npcap",
)


def _is_bogus_subnet(ip: str) -> bool:
    return ip.startswith(_BOGUS_SUBNET_PREFIXES)


def guess_subnet(verbose: bool = False) -> str | None:
    """猜本机所在网段的前三段（会主动跳过代理 TUN / 虚拟网卡）。"""
    candidates: list[tuple[str, str]] = []   # (ip, iface)

    # 1) 内核路由表：destination=0.0.0.0 的那条就是默认出口
    #    route print 0.0.0.0 / ip route 都能拿到「接口」列，可以据此排除 TUN
    try:
        if IS_WINDOWS:
            out = subprocess.run(["route", "print", "0.0.0.0"],
                                 capture_output=True, timeout=10
                                 ).stdout.decode("gbk", errors="replace")
            for line in out.splitlines():
                parts = line.split()
                # 0.0.0.0  0.0.0.0   <gateway>  <iface-ip>  <metric>
                if len(parts) >= 4 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                    cand = parts[3]
                    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", cand):
                        candidates.append((cand, ""))
        else:
            out = subprocess.run(["ip", "-o", "route", "get", "1.1.1.1"],
                                 capture_output=True, timeout=10
                                 ).stdout.decode("utf-8", errors="replace")
            m = re.search(r"src (\d+\.\d+\.\d+\.\d+).*?dev (\S+)", out)
            if m:
                candidates.append((m.group(1), m.group(2)))
    except (OSError, subprocess.SubprocessError):
        pass

    # 2) 所有非回环 IPv4
    try:
        if IS_WINDOWS:
            out = subprocess.run(["ipconfig"], capture_output=True,
                                 timeout=10).stdout.decode("gbk", errors="replace")
            for m in re.finditer(r"IPv4[^:]*:\s*([\d.]+)", out):
                candidates.append((m.group(1), ""))
        else:
            out = subprocess.run(["ip", "-o", "addr"], capture_output=True,
                                 timeout=10).stdout.decode("utf-8", errors="replace")
            for line in out.splitlines():
                m = re.search(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    candidates.append((m.group(2), m.group(1)))
    except (OSError, subprocess.SubprocessError):
        pass

    # 3) UDP socket 兜底（拿到的通常就是默认出口 IP，但也可能是 TUN）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        candidates.append((s.getsockname()[0], ""))
        s.close()
    except OSError:
        pass

    picked = None
    for ip, iface in candidates:
        if _is_bogus_subnet(ip):
            if verbose:
                print(f"  [跳过] {ip:<16} 属于保留/隧道网段（{iface or '?'}）")
            continue
        if iface and any(h in iface.lower() for h in _VIRTUAL_IF_HINTS):
            if verbose:
                print(f"  [跳过] {ip:<16} 走的是虚拟网卡 {iface}")
            continue
        if ip.startswith("172."):
            # 172.16–172.31 才是私网，172.17 是 Docker 默认桥
            second = int(ip.split(".")[1])
            if not (16 <= second <= 31):
                continue
        picked = ".".join(ip.split(".")[:3])
        if verbose:
            print(f"  [选中] {picked}.0/24  （来自 {ip} {iface}）")
        break
    return picked


# ══════════════════════════════════════════════════════════════════════
# 探测原语
# ══════════════════════════════════════════════════════════════════════

def ping(ip: str, timeout_ms: int = 400) -> tuple[str, str] | None:
    """ping 一次。返回 (ip, 延迟) 或 None。"""
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
        enc = "gbk"
    else:
        # Linux/macOS 的 -W 单位是秒
        cmd = ["ping", "-c", "1", "-W", "1", ip]
        enc = "utf-8"

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_ms / 1000 + 2)
    except (OSError, subprocess.SubprocessError):
        return None

    out = r.stdout.decode(enc, errors="replace")
    if "TTL=" not in out.upper():
        return None
    m = re.search(r"[时間间][=\s]*[<]?\s*(\d+(?:\.\d+)?)\s*ms", out)
    if not m:
        m = re.search(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", out)
    return (ip, m.group(1) if m else "?")


def probe_port(ip: str, port: int, timeout: float = 0.7) -> bool:
    """TCP 连接探测。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def grab_banner(ip: str, port: int = 22, timeout: float = 1.5) -> str:
    """抓服务 banner（SSH 主动发；HTTP 需要先发请求）。"""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            if port in (80, 8080, 3000, 5000):
                s.sendall(b"HEAD / HTTP/1.0\r\nHost: x\r\n\r\n")
            try:
                data = s.recv(512)
            except (socket.timeout, OSError):
                return ""
            return data.decode("utf-8", errors="replace").strip()
    except (OSError, socket.timeout):
        return ""


def arp_lookup(ip: str) -> str:
    """查 MAC 地址（用于厂商识别）。"""
    try:
        if IS_WINDOWS:
            out = subprocess.run(["arp", "-a", ip], capture_output=True,
                                 timeout=5).stdout.decode("gbk", errors="replace")
            m = re.search(r"([0-9a-f]{2}[-:]){5}[0-9a-f]{2}", out, re.I)
        else:
            out = subprocess.run(["ip", "neigh", "show", ip], capture_output=True,
                                 timeout=5).stdout.decode("utf-8", errors="replace")
            m = re.search(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", out, re.I)
        return m.group(0).replace("-", ":").lower() if m else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def vendor_of(mac: str) -> str:
    """按 OUI 猜厂商。"""
    if not mac or len(mac) < 8:
        return ""
    prefix = mac[:8].lower()
    if prefix in OUI_VENDORS:
        return OUI_VENDORS[prefix]
    # 本地管理地址（首字节 bit1 = 1）说明是随机 MAC
    try:
        if int(mac.split(":")[0], 16) & 0x02:
            return "随机 MAC（隐私地址）"
    except (ValueError, IndexError):
        pass
    return ""


# ══════════════════════════════════════════════════════════════════════
# 主机画像
# ══════════════════════════════════════════════════════════════════════

def profile_host(ip: str, rtt: str, ports: list[int], deep: bool) -> dict:
    info: dict = {
        "ip": ip, "rtt_ms": rtt, "open_ports": [], "services": [],
        "banner": "", "mac": "", "vendor": "", "os_hint": "", "verdict": "",
    }

    for p in ports:
        if probe_port(ip, p):
            info["open_ports"].append(p)
            info["services"].append(f"{p}/{PORT_NAMES.get(p, '?')}")

    if deep:
        if 22 in info["open_ports"]:
            b = grab_banner(ip, 22)
            info["banner"] = b
            for pat, name in SSH_BANNER_HINTS:
                if re.search(pat, b, re.I):
                    info["os_hint"] = name
                    break
        elif info["open_ports"]:
            info["banner"] = grab_banner(ip, info["open_ports"][0])

        info["mac"] = arp_lookup(ip)
        info["vendor"] = vendor_of(info["mac"])

    if 22 in info["open_ports"]:
        info["verdict"] = "Linux 设备（SSH 可用）← 大概率就是要找的板子"
    elif info["open_ports"]:
        info["verdict"] = f"有服务在跑（{info['services'][0]}），但无 SSH"
    else:
        info["verdict"] = "只响应 ping（可能禁了其它服务，或有防火墙）"

    return info


# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="局域网设备发现与识别（跨平台）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python lan_sweep.py                       # 自动猜网段
  python lan_sweep.py 192.168.10            # 指定网段
  python lan_sweep.py 192.168.10 --identify # 深挖身份（较慢）
  python lan_sweep.py 192.168.10 --json     # 机器可读
""")
    ap.add_argument("--subnet", dest="subnet_opt",
                    help="网段前三段，如 192.168.10（等价于位置参数）")
    ap.add_argument("--ports", default=",".join(map(str, DEFAULT_PORTS)),
                    help=f"要探测的端口（默认 {','.join(map(str, DEFAULT_PORTS))}）")
    ap.add_argument("--identify", action="store_true",
                    help="抓 banner 与 MAC 厂商（更慢但更准）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--timeout", type=int, default=400, help="ping 超时毫秒（默认 400）")
    ap.add_argument("--workers", type=int, default=64, help="并发数（默认 64）")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="打印网段推断过程（为什么选了这个网段）")

    # 位置参数放最后：nargs='?' 且只有当它看起来不像一个选项时才吃。
    # 之前把它放在最前，导致 `lan_sweep.py --identify` 里 --identify 被当成网段，
    # 扫描目标成了 "--identify.0/24"（0 台设备），白白浪费一次排查。
    ap.add_argument("subnet", nargs="?",
                    help="网段前三段，如 192.168.10（默认自动推断）")

    args = ap.parse_args()

    # 位置参数误吃选项的兜底：把 "--foo" 塞回 argv 重新解析一次
    if args.subnet and args.subnet.startswith("-"):
        fixed = [a for a in sys.argv[1:] if a != args.subnet]
        fixed.append(args.subnet)
        args = ap.parse_args(fixed)

    subnet = args.subnet_opt or args.subnet
    if subnet:
        if _is_bogus_subnet(subnet.rstrip(".") + ".1"):
            print(f"⚠️  {subnet}.0/24 看着像代理 TUN / 保留网段，"
                  f"板子不会在这里。请显式指定，如： python {sys.argv[0]} 192.168.10")
    else:
        if args.verbose and not args.json:
            print("推断网段：")
        subnet = guess_subnet(verbose=args.verbose and not args.json)
        if not subnet:
            print("推断不出网段，请显式指定，如： python lan_sweep.py 192.168.10")
            return 2
        if not args.json:
            print(f"（自动推断网段，可用位置参数覆盖）")
    subnet = subnet.rstrip(".")
    if not re.fullmatch(r"\d+\.\d+\.\d+", subnet):
        print(f"网段格式应为三段数字（如 192.168.10），收到：{subnet}")
        return 2

    try:
        ports = [int(x) for x in args.ports.split(",") if x.strip()]
    except ValueError:
        print("--ports 格式错误，应如 22,80,443")
        return 2

    if not args.json:
        print(f"扫描 {subnet}.0/24   端口 {ports}   "
              f"{'含身份识别' if args.identify else '仅探活'}")
        print()

    t0 = time.time()
    targets = [f"{subnet}.{i}" for i in range(1, 255)]

    alive: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(lambda ip: ping(ip, args.timeout), targets):
            if res:
                alive.append(res)

    results: list[dict] = []
    if alive:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.workers, 32)) as ex:
            futs = [ex.submit(profile_host, ip, rtt, ports, args.identify)
                    for ip, rtt in alive]
            for f in concurrent.futures.as_completed(futs):
                try:
                    results.append(f.result())
                except Exception:
                    pass

    results.sort(key=lambda r: tuple(int(x) for x in r["ip"].split(".")))

    if args.json:
        print(json.dumps({
            "subnet": subnet, "scanned": len(targets), "alive": len(alive),
            "elapsed_s": round(time.time() - t0, 1), "hosts": results,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"发现 {len(results)} 台在线设备（耗时 {time.time()-t0:.1f}s）")
    print("─" * 76)

    with_ssh = [r for r in results if 22 in r["open_ports"]]
    others = [r for r in results if 22 not in r["open_ports"]]

    if with_ssh:
        print()
        print("★ Linux 设备（SSH 可用）—— 开发板/服务器通常在这里")
        print()
        for r in with_ssh:
            print(f"  {r['ip']:<16} {r['rtt_ms']:>6}ms  {', '.join(r['services'])}")
            if r["vendor"]:
                print(f"  {'':16} 厂商: {r['vendor']}")
            if r["banner"]:
                print(f"  {'':16} banner: {r['banner'][:70]}")
            if r["os_hint"]:
                print(f"  {'':16} 系统线索: {r['os_hint']}")

    if others:
        print()
        print("其他设备")
        print()
        for r in others:
            svc = ", ".join(r["services"]) if r["services"] else "无开放端口"
            print(f"  {r['ip']:<16} {r['rtt_ms']:>6}ms  {svc}")
            if r["vendor"]:
                print(f"  {'':16} 厂商: {r['vendor']}")

    print()
    print("─" * 76)
    print("提示:")
    if with_ssh:
        print(f"  要连的板子大概率是：{with_ssh[0]['ip']}")
        print(f"  试: ssh <user>@{with_ssh[0]['ip']}")
    else:
        print("  没有发现 SSH 设备 —— 板子可能没起来，或不在这个网段")
        print("  排查顺序:")
        print("    1) 串口看有没有输出（tools/baud_scan.py）")
        print("    2) 板子上电后立刻跑本脚本，看有没有 IP 出现")
        print("    3) 蓝色状态灯是否在闪（闪 = 系统正常启动）")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
