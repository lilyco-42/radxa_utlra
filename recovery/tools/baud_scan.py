#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A 串口波特率扫描 —— 解决"收到几万字节但读不出字"的问题。

背景（2026-09-18）：
    在 COM3 以 1500000 打开，收到 51,828 字节，但几乎全是空白/NUL，
    没有任何可读文本，也没有提示符。

    这个现象有两种解释：
      ① 波特率不对 —— 板子实际用别的速率（日志 bootargs 写的是 console=ttyAS0,115200n8）
      ② 线上是噪声 —— 没接好 / TX 与 RX 接反 / 板子没真在输出

    区分方法：把每个候选波特率都试一遍，统计"可打印字符占比"。
    真波特率那档会明显出现 U-Boot / Debian / => / login 这类可读串，
    其它档位全是乱码。

原理：
    串口乱码的本质是采样错位，产生的是"看起来随机但每个字节都是可打印
    范围外的高位字节"或"大量 NUL"。所以用两个指标打分：
      - printable_ratio  可打印 ASCII 占比（含 \\r\\n\\t）
      - usable_text      是否命中已知关键词

用法：
    # 扫一遍所有候选波特率，每档听 6 秒（板子需要有输出才有效）
    python baud_scan.py

    # 只在指定几档里扫
    python baud_scan.py --bauds 115200,1500000

    # 每档听久一点（比如板子还在 autoboot 倒计时，输出稀疏）
    python baud_scan.py --listen 12

    # 边扫边存每个档位的原始字节，便于事后 hexdump
    python baud_scan.py --dump-dir bauddumps

提示：
    * 扫描期间最好让板子"有话说"：可以按一下 RESET，让它重新打一遍启动日志。
    * 若所有档位都 <30% 可打印且无关键词 → 不是波特率问题，是接线/供电问题。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

try:
    import serial
except ImportError:
    raise SystemExit("缺少 pyserial，请用 venv 解释器跑。")

# 候选波特率：A7A 通常 115200；1500000 是很多 Allwinner/Radxa 调试图省时间用的
DEFAULT_BAUDS = [
    115200,
    1500000,
    921600,
    57600,
    38400,
    9600,
    230400,
    460800,
    3000000,
    2000000,
]

# 命中这些关键词基本可以确定是真波特率
KEYWORDS = [
    "U-Boot", "uboot", "SPL", "Debian", "GNU/Linux", "login:",
    "=>", "sunxi", "Allwinner", "initramfs", "mmc", "Kernel",
    "systemd", "ttyAS0", "root@", "Starting kernel",
]

# 可打印判定：可见字符 + 常用空白控制符
PRINTABLE = set(range(0x20, 0x7F)) | {0x0A, 0x0D, 0x09}


def score(raw: bytes) -> dict:
    n = len(raw)
    if n == 0:
        return {"n": 0, "printable": 0.0, "keys": [], "nul": 0.0}

    printable = sum(1 for b in raw if b in PRINTABLE)
    nul = raw.count(0)
    text = raw.decode("latin-1", errors="replace")
    keys = sorted({k for k in KEYWORDS if k in text})
    return {
        "n": n,
        "printable": printable / n,
        "keys": keys,
        "nul": nul / n,
    }


def sample(port: str, baud: int, seconds: float) -> tuple[bytes, str | None]:
    """在某档波特率上听一段时间，返回 (原始字节, 错误信息)。"""
    try:
        ser = serial.Serial(port, baud, timeout=0.15, write_timeout=2.0)
    except serial.SerialException as exc:
        return b"", str(exc)

    chunks: list[bytes] = []
    try:
        # 清一下输入缓冲
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

        end = time.time() + seconds
        while time.time() < end:
            try:
                waiting = ser.in_waiting
            except Exception:
                waiting = 0
            try:
                data = ser.read(waiting or 1)
            except serial.SerialException as exc:
                return b"".join(chunks), str(exc)
            if data:
                chunks.append(data)
    finally:
        try:
            ser.close()
        except Exception:
            pass

    return b"".join(chunks), None


def snippet(raw: bytes, limit: int = 120) -> str:
    """从原始字节里抠出一段可读文本用于展示。"""
    text = raw.decode("latin-1", errors="replace")
    # 压掉长空白
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "·", text)
    text = re.sub(r"[\r\n]+", " / ", text)
    return text[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description="A7A 串口波特率扫描")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--bauds", default=None,
                    help="逗号分隔的候选波特率，默认扫内置列表")
    ap.add_argument("--listen", type=float, default=6.0,
                    help="每档听多少秒（默认 6）")
    ap.add_argument("--dump-dir", default=None,
                    help="把每档的原始字节写到这个目录")
    args = ap.parse_args()

    bauds = (DEFAULT_BAUDS if not args.bauds
             else [int(x) for x in args.bauds.split(",") if x.strip()])

    print("=" * 72)
    print(f"串口波特率扫描  port={args.port}  每档 {args.listen:g}s  共 {len(bauds)} 档")
    print("=" * 72)
    print("[i] 建议现在按一下板子的 RESET，让它在扫描期间持续输出启动日志。")
    print("[i] 倒计时 3 秒后开始…\n")
    time.sleep(3)

    if args.dump_dir:
        os.makedirs(args.dump_dir, exist_ok=True)

    results = []
    for baud in bauds:
        print(f"--- {baud} bps --- ", end="", flush=True)
        raw, err = sample(args.port, baud, args.listen)
        if err:
            print(f"打不开/读失败: {err}")
            results.append((baud, {"n": 0, "printable": 0.0, "keys": [], "nul": 0.0}, b""))
            continue

        s = score(raw)
        verdict = "空" if s["n"] == 0 else f"{s['printable']*100:.1f}% 可打印"
        if s["keys"]:
            verdict += "  ★命中: " + ",".join(s["keys"][:5])
        print(f"{s['n']} 字节  {verdict}")
        if s["n"]:
            print(f"      样本: {snippet(raw)}")

        if args.dump_dir:
            path = os.path.join(args.dump_dir, f"baud_{baud}.bin")
            with open(path, "wb") as fh:
                fh.write(raw)
            print(f"      已存: {path}")

        results.append((baud, s, raw))

    # ── 汇总排名 ──
    print("\n" + "=" * 72)
    print("【汇总】按「可打印比例 + 关键词命中」排序")
    print("=" * 72)

    def rank(item):
        baud, s, _ = item
        return (len(s["keys"]), s["printable"], s["n"])

    ranked = sorted(results, key=rank, reverse=True)
    for baud, s, _ in ranked:
        star = "★" if s["keys"] else (" " if s["printable"] > 0.7 else "?")
        print(f"  {star} {baud:>8} bps   {s['n']:>6} 字节   "
              f"{s['printable']*100:5.1f}% 可打印   "
              f"NUL {s['nul']*100:4.1f}%   "
              f"关键词 {','.join(s['keys'][:4]) or '无'}")

    best = ranked[0]
    print()
    if best[1]["keys"]:
        print(f"[✓] 最可能的波特率是 {best[0]} —— 出现了 U-Boot/系统关键词。")
        print(f"    后续所有工具都加 --baud {best[0]} 即可。")
        return 0

    if all(r[1]["n"] == 0 for r in results):
        print("[×] 所有档位都是 0 字节 —— 板子根本没往串口发东西。")
        print("    这指向：① 板子没通电  ② TX/RX 接反  ③ 串口线/适配器坏。")
        print("    请对照 POWER-CHECKLIST.md 第 3 步（插头/接线）。")
        return 1

    if best[1]["printable"] < 0.7:
        print("[×] 没有任何档位出现可读文本，且最佳档位可打印率仍很低。")
        print("    → 不是波特率问题，是物理层问题（接线/接地/供电）。")
        print("    请确认：GND 是否共地？TX↔RX 是否交叉？板子是否真在跑？")
        return 1

    print(f"[?] 最佳档位是 {best[0]}（可打印 {best[1]['printable']*100:.1f}%），")
    print("    但没命中已知关键词。可能是板子当时没输出完整日志 ——")
    print(f"    建议按 RESET 重跑：python baud_scan.py --bauds {best[0]} --listen 20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
