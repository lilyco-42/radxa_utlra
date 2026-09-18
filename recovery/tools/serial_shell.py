#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""串口 TX 探测 —— 板子活着但网络不通时，试着从串口拿 shell。

## 背景

2026-09-18 实测：板子蓝灯闪、systemd 在 1410 秒、journald 报 I/O 错误，
说明**系统是活的**，但没有 IP（NetworkManager 挂），所以 SSH 走不通。

唯一的路是串口 stdin。之前用户说 `回车没用`，但那时文件系统状态不同。

## 这个脚本做什么

1. 打开串口，静默听 3 秒，确认 RX 正常
2. 连发 3 个回车，看有没有 login: / 提示符 / 命令回显
3. 如果有回显 → 发一串**只读**诊断命令，把结果抓回来
4. 如果没回显 → 尝试 Ctrl+C、Ctrl+D 唤醒，再试一次
5. 报告结论：TX 通 / TX 不通

## 安全边界

发出的命令**全部只读**：
    cat /sys/...    dmesg    systemctl status    lsblk    mount

**绝不发送**：任何写入、格式、fsck -y、setenv、reboot。
最后一条命令是 `echo ===DONE===`，仅用于确认链路。
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import serial  # type: ignore

READONLY_CMDS = [
    ("uname",     "uname -a"),
    ("mount",     "mount | grep -E 'mmcblk|root'"),
    ("ro_check",  "grep -E ' / ' /proc/mounts"),
    ("partitions","cat /proc/partitions"),
    ("card_name", "cat /sys/block/mmcblk1/device/name 2>/dev/null"),
    ("card_life", "cat /sys/block/mmcblk1/device/life_time 2>/dev/null"),
    ("mmc_err",   "dmesg | grep -iE 'mmc|smc|sunxi_mmc' | tail -30"),
    ("ext4_err",  "dmesg | grep -iE 'EXT4-fs|I/O error|readonly' | tail -20"),
    ("failed_svc","systemctl --failed --no-pager --no-legend 2>/dev/null | head -25"),
    ("net",       "ip -br addr 2>/dev/null"),
]

PROMPT_RE = re.compile(r"(login:|Password:|[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+:.*[$#]|^[#$]\s|\(initramfs\)|~ ?[$#])", re.M)


class Console:
    def __init__(self, port: str, baud: int):
        self.ser = serial.Serial(port, baud, timeout=0.3)
        self.buf = ""
        # 明确拉低 DTR/RTS 之外的状态，避免某些 FTDI 因握手线震荡复位板子
        try:
            self.ser.dtr = False
            self.ser.rts = False
        except Exception:  # noqa: BLE001
            pass

    def pump(self, secs: float) -> str:
        end = time.time() + secs
        got = ""
        while time.time() < end:
            d = self.ser.read(8192)
            if d:
                got += d.decode("utf-8", errors="replace")
        self.buf += got
        return got

    def send(self, line: str, wait: float = 2.0) -> str:
        self.ser.write(line.encode() + b"\n")
        self.ser.flush()
        return self.pump(wait)

    def send_raw(self, data: bytes, wait: float = 1.5) -> str:
        self.ser.write(data)
        self.ser.flush()
        return self.pump(wait)

    def close(self) -> None:
        self.ser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="串口 TX 探测：能不能拿到 shell")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--listen", type=float, default=3.0, help="开场静默收听秒数")
    ap.add_argument("--save", help="把全部原始输出存文件")
    args = ap.parse_args()

    print(f"[i] 打开 {args.port} @ {args.baud}")
    c = Console(args.port, args.baud)

    print(f"[i] 静默收听 {args.listen:.0f} 秒，确认 RX…")
    head = c.pump(args.listen)
    printable = sum(1 for ch in head if 32 <= ord(ch) < 127 or ch in "\r\n\t")
    ratio = (printable / len(head) * 100) if head else 0.0
    print(f"    收到 {len(head)} 字节，可打印率 {ratio:.1f}%")
    if head.strip():
        sample = head.strip().splitlines()[-1][:110]
        print(f"    最后一行：{sample}")

    print()
    print("[1] 连发 3 个回车，看有没有提示符/回显…")
    before = c.buf
    resp = ""
    for _ in range(3):
        resp += c.send("", 1.2)

    # 关键：把"回车之前就在刷的 journald 噪音"从响应里剔除，
    # 否则会把刷屏误判成"板子有反应"。2026-09-18 踩过这个坑。
    noise = set()
    for l in before.splitlines():
        if l.strip():
            noise.add(re.sub(r"\[[\d.\s]+\]|\[ *T?\d+\]|\d+", "N", l)[:90])
    fresh_lines = []
    for l in resp.splitlines():
        if not l.strip():
            continue
        key = re.sub(r"\[[\d.\s]+\]|\[ *T?\d+\]|\d+", "N", l)[:90]
        if key not in noise:
            fresh_lines.append(l.strip())
    resp_fresh = "\n".join(fresh_lines)

    has_prompt = bool(PROMPT_RE.search(resp_fresh))
    has_echo = len(fresh_lines) > 0

    if has_prompt:
        print("    ✓ 检测到提示符/登录提示！TX 通道可用")
        print(f"    片段：{resp_fresh[-200:]}")
    elif has_echo:
        print(f"    ? 有 {len(fresh_lines)} 行新输出（非刷屏噪音）：")
        for l in fresh_lines[-5:]:
            print(f"      {l[:130]}")
    else:
        print("    ✗ 回车无任何新反应（刷屏噪音已剔除）")
        print()
        print("[2] 尝试 Ctrl+C 唤醒…")
        c.send_raw(b"\x03", 1.5)
        print("[3] 再试 Ctrl+D…")
        c.send_raw(b"\x04", 1.5)
        print("[4] 再发两个回车…")
        resp2 = ""
        for _ in range(2):
            resp2 += c.send("", 1.5)
        fresh2 = [
            l.strip() for l in resp2.splitlines()
            if l.strip() and re.sub(r"\[[\d.\s]+\]|\[ *T?\d+\]|\d+", "N", l)[:90] not in noise
        ]
        if fresh2 or PROMPT_RE.search(resp2):
            print("    ✓ 唤醒后有新反应了！")
            for l in (fresh2 or resp2.splitlines())[-5:]:
                print(f"      {l[:130]}")
            has_prompt = True
        else:
            print("    ✗ 依然无反应")

    if not has_prompt:
        print()
        print("=" * 68)
        print("结论：串口 TX（电脑 → 板子）不通。")
        print()
        print("★ 2026-09-18 实测确认的头号原因：")
        print("  板子侧的 serial-getty@ttyAS0 没有起来。")
        print()
        print("  证据：抓了 1.27 MB / 45,249 行串口输出，")
        print("        其中 getty=0 次、login:=0 次、ttyAS0=0 次。")
        print("  原因：文件系统被强制只读 → systemd 无法启动该 unit。")
        print("        （同一根因的另一个症状，与 NetworkManager 挂掉一致）")
        print()
        print("  → 也就是说：这不是串口线坏，修好文件系统串口自然就通。")
        print()
        print("次要可能：")
        print("  · 串口线只有 RX 通、TX 线断（FTDI TXD 要接板子 RXD）")
        print("  · 板子完全没接 TX（有些排线只做 3 线，缺 TXD）")
        print()
        print("注意：U-Boot 阶段的 console 是独立于系统的，")
        print("      那时按回车一定有反应 —— 可用它验证 TX 线本身好没好。")
        print("=" * 68)
    else:
        print()
        print("=" * 68)
        print("TX 可用！开始跑只读诊断命令…")
        print("=" * 68)
        for name, cmd in READONLY_CMDS:
            print(f"\n── {name} ──")
            print(f"$ {cmd}")
            out = c.send(cmd, 3.0)
            out = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", out)
            lines = [l for l in out.splitlines() if l.strip()]
            for l in lines[-18:]:
                print(f"  {l[:150]}")
        print()
        c.send("echo ===PROBE_DONE===", 1.0)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(c.buf)
        print(f"\n[i] 原始输出已存 {args.save}")

    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
