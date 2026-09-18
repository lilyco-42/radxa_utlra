#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A 多通道探测 —— 板子只要有任何一条路"活过来"，就能立刻发现。

为什么需要它：
  A7A 卡在 initramfs 时没有网络，串口是唯一入口；但串口也有"线接反/TX RX 错"
  的物理风险。本工具同时盯 4 条通道，避免"死盯一条路"：

    通道 1  串口（COM3, 115200）      —— initramfs 唯一入口
    通道 2  局域网扫描（ICMP）          —— 系统起来后会出现在网里
    通道 3  SSH 端口 22（TCP）          —— 比 ping 更可靠（有些系统禁 ping）
    通道 4  USB 总线（Allwinner VID）   —— 板子插 USB 或进 fastboot/刷机模式

  一旦任一通道有信号，就立刻打印"板子活了，走 X 通道"。

用法：
  # 板子插好线、上电前就开始跑，它会一直等
  python probe_a7a.py

  # 指定网段
  python probe_a7a.py --subnet 192.168.10.0/24

  # 静默模式，只输出结论
  python probe_a7a.py --quiet

  # 限定探测时长
  python probe_a7a.py --duration 600
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import subprocess
import sys
import threading
import time

# Allwinner 的 USB VID（A7A/A733 刷机模式或 USB 设备会出现）
ALLWINNER_VIDS = ("1F3A", "18C5", "1F3C")

# 需要排除的"自己人"网段/主机（避免把自己的 IP 当板子）
SELF_HINTS = ("192.168.10.218",)

try:
    import serial
except ImportError:
    serial = None


class Reporter:
    """线程安全的输出。"""

    def __init__(self, quiet: bool = False) -> None:
        self.lock = threading.Lock()
        self.quiet = quiet

    def say(self, msg: str, force: bool = False) -> None:
        if self.quiet and not force:
            return
        with self.lock:
            print(msg, flush=True)

    def found(self, channel: str, detail: str) -> None:
        with self.lock:
            print("\n" + "★" * 70, flush=True)
            print(f"★  板子活了！通道：{channel}", flush=True)
            print(f"★  {detail}", flush=True)
            print("★" * 70 + "\n", flush=True)


# ──────────────────── 通道 1：串口 ────────────────────


def probe_serial(port: str, baud: int, rep: Reporter, stop: threading.Event,
                 found: dict) -> None:
    if serial is None:
        rep.say("[通道1 串口] 跳过：没装 pyserial")
        return
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except Exception as exc:
        rep.say(f"[通道1 串口] 打不开 {port}: {exc}", force=True)
        return

    rep.say(f"[通道1 串口] 已监听 {port} @ {baud}（等板子输出…）", force=True)
    try:
        while not stop.is_set():
            data = ser.read(ser.in_waiting or 1)
            if data:
                text = data.decode("utf-8", errors="replace")
                with rep.lock:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                if "serial" not in found:
                    found["serial"] = port
                    rep.found("串口", f"{port} 收到 {len(data)} 字节，板子已在输出启动日志")
    except Exception as exc:
        rep.say(f"\n[通道1 串口] 读失败: {exc}", force=True)
    finally:
        try:
            ser.close()
        except Exception:
            pass


# ──────────────────── 通道 2/3：网络 ────────────────────


def ping_ok(ip: str, timeout_ms: int = 400) -> bool:
    try:
        return subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout_ms / 1000 + 2,
        ).returncode == 0
    except Exception:
        return False


def tcp_ok(ip: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_network(subnet: str, rep: Reporter, stop: threading.Event,
                  found: dict, interval: float = 8.0) -> None:
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        rep.say(f"[通道2 网络] 网段非法: {subnet}", force=True)
        return

    hosts = [str(h) for h in net.hosts()][:1024]
    rep.say(f"[通道2 网络] 每 {interval:.0f} 秒扫一次 {subnet}（{len(hosts)} 台）", force=True)

    baseline: set[str] = set()
    first = True

    while not stop.is_set():
        alive: set[str] = set()

        # 并发 ping
        procs = []
        for h in hosts:
            try:
                procs.append((h, subprocess.Popen(
                    ["ping", "-n", "1", "-w", "300", h],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )))
            except Exception:
                pass
        for h, p in procs:
            try:
                if p.wait(timeout=3) == 0:
                    alive.add(h)
            except subprocess.TimeoutExpired:
                p.kill()

        known = alive - set(SELF_HINTS)
        if first:
            baseline = known
            rep.say(f"[通道2 网络] 基线设备: {', '.join(sorted(baseline)) or '无'}")
            first = False
        else:
            new = known - baseline
            if new:
                # 新出现的设备，验证 22 端口
                for ip in sorted(new):
                    if tcp_ok(ip, 22):
                        found["network"] = ip
                        rep.found("网络 SSH", f"新设备 {ip} 的 22 端口开着 —— 就是板子")
                        return
                rep.say(f"[通道2 网络] 新设备（22 未开）: {', '.join(sorted(new))}")

        # 顺带查 TCP 22，防止系统禁 ping
        if not stop.is_set():
            for ip in sorted(known - baseline):
                if tcp_ok(ip, 22):
                    found["network"] = ip
                    rep.found("网络 SSH", f"{ip} 的 22 端口开着")
                    return

        stop.wait(interval)


# ──────────────────── 通道 4：USB ────────────────────


def probe_usb(rep: Reporter, stop: threading.Event, found: dict,
              interval: float = 5.0) -> None:
    rep.say("[通道4 USB] 监视 Allwinner 设备出现（刷机模式/OTG）", force=True)
    seen: set[str] = set()

    while not stop.is_set():
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -PresentOnly | Where-Object {$_.InstanceId -like 'USB*'} "
                 "| Select-Object -ExpandProperty InstanceId"],
                capture_output=True, text=True, timeout=25,
            )
            ids = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        except Exception:
            ids = []

        for i in ids:
            upper = i.upper()
            for vid in ALLWINNER_VIDS:
                if f"VID_{vid}" in upper and i not in seen:
                    seen.add(i)
                    found["usb"] = i
                    rep.found("USB", f"发现 Allwinner 设备: {i}")
                    return
        stop.wait(interval)


# ──────────────────── 主流程 ────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="A7A 多通道探测")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--subnet", default="192.168.10.0/24")
    ap.add_argument("--duration", type=float, default=0, help="探测秒数，0=一直跑")
    ap.add_argument("--quiet", action="store_true", help="只输出结论，不刷串口原文")
    args = ap.parse_args()

    rep = Reporter(quiet=args.quiet)
    stop = threading.Event()
    found: dict = {}

    print("╔" + "═" * 68 + "╗")
    print("║  A7A 多通道探测 —— 等板子活过来".ljust(58) + "║")
    print("╚" + "═" * 68 + "╝")
    print("\n现在请：① 给板子通电  ② 确认串口线接在板子上  ③ 等")
    print("任何一条通道有信号，我立刻告诉你。按 Ctrl+C 结束。\n")

    threads = [
        threading.Thread(target=probe_serial,
                         args=(args.port, args.baud, rep, stop, found), daemon=True),
        threading.Thread(target=probe_network,
                         args=(args.subnet, rep, stop, found), daemon=True),
        threading.Thread(target=probe_usb,
                         args=(rep, stop, found), daemon=True),
    ]
    for t in threads:
        t.start()

    deadline = time.time() + args.duration if args.duration else None
    try:
        while not stop.is_set():
            if found:
                break
            if deadline and time.time() > deadline:
                print("\n[超时] 探测时间到了，没有任何通道发现板子。")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[中断]")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2)

    # 结果汇总
    print("\n" + "─" * 70)
    if found:
        print("结果：板子已发现")
        for k, v in found.items():
            print(f"  {k:8} → {v}")
        if "network" in found:
            ip = found["network"]
            print("\n下一步：")
            print(f"  ssh radxa@{ip}")
            print(f"  scp a7a-rebuild.tar.gz radxa@{ip}:~/")
            print(f"  ssh radxa@{ip} 'tar xzf a7a-rebuild.tar.gz && ./a7a-rebuild/deploy-rebuild.sh --check'")
        elif "serial" in found:
            print("\n串口有输出但网络没通 —— 板子卡在早期阶段。")
            print("  跑：python rescue_a7a.py")
    else:
        print("结果：所有通道都没有发现板子。")
        print("\n按这个顺序检查（几乎一定是前两项）：")
        print("  1. 电源适配器是不是 5V/3A 以上？换个好的电源试")
        print("  2. 串口线的 TX/RX 是不是接反了？（TX→RX, RX→TX）")
        print("  3. 板子上有没有电源开关/跳线没开？")
        print("  4. DC 插头有没有插到底？")
        print("  5. 若都不行，万用表量板子 5V 对地电压")
    print("─" * 70)
    return 0 if found else 6


if __name__ == "__main__":
    raise SystemExit(main())
