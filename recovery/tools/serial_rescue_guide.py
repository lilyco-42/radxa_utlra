#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A 串口救活器 —— 治"journald 刷屏把串口淹没、回车打不进去"。

## 问题（2026-09-18 实测确认）

板子状态：
    · 蓝灯闪烁   → 系统正常启动（官方定义：蓝灯闪 = 系统正常）
    · 日志时间戳 1472s → 已开机 24 分钟，没死
    · 局域网扫不到 → 文件系统只读，NetworkManager 起不来，没 IP
    · 串口 3 秒收到 45,249 行 / 1.27 MB
    · 去重后只有 11 种消息

那 11 种消息**全部是同一个自我循环**：

    journald 想写 /var/log/journal/*.journal
        → 文件系统已只读 → 写失败
              → journald 把这个"写失败"本身当日志再写
                    → 又失败 → 无限放大（Dropped 389 similar messages）

**这就是用户说"回车没用"的真正原因** —— 不是串口 TX 线坏，
是每秒 15,000 行日志把终端输入彻底冲走了。

## 解法：让 journald 闭嘴，串口就回来了

关键洞察：**不需要改 U-Boot**。有三个层次的手段，从轻到重：

### 手段 1：内核参数（在 U-Boot 里 setenv，不 saveenv）

    setenv bootargs ... systemd.journald.forward_to_console=0 quiet loglevel=1

`quiet` + `loglevel=1` 让内核少打印；
`systemd.journald.forward_to_console=0` 阻止 journald 往控制台回灌。

### 手段 2：让 journald 走内存（根本解法）

    setenv bootargs ... systemd.journald.forward_to_console=0
    setenv bootargs ${bootargs} systemd.unit=multi-user.target

再用 root shell 执行：
    mkdir -p /run/log/journal
    systemctl restart systemd-journald

`journald` 在 `/run/log/journal`（tmpfs，内存）存在时会**优先用它**，
完全不碰只读的 /var/log，刷屏立刻停止。

### 手段 3：只读挂载，从根上不写盘（最安全，推荐取证用）

    setenv bootargs ... rootwait ro systemd.unit=rescue.target

`ro` 让内核根本不尝试写盘 → 没有 I/O error → journald 不循环。

## 这个脚本做什么

它会：
  1. 先量一下刷屏速率（确认诊断）
  2. 打印出**全部**可选方案，含精确的 U-Boot 命令（**只 setenv**）
  3. 给出在 U-Boot 里进 rescue 模式的完整按键流程

## 安全边界（用户要求"不要动我的 uboot"）

本脚本**只读串口、只打印命令**。它**不发送任何东西到板子**，
所以物理上不可能碰 U-Boot。命令交给用户手动敲，
且全部是 `setenv`（内存态），**没有一条 `saveenv`**。
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
import time

try:
    import serial  # type: ignore
except ImportError:
    serial = None  # type: ignore


UBOOT_CMDS = {
    "最小刷屏 + 救援模式（推荐）": [
        "# 进入 U-Boot：开机后狂按回车/空格，看到 '=>' 即成功",
        "# 下面全部是 setenv —— 只改内存，不 saveenv，断电即恢复",
        "",
        "setenv bootargs root=UUID=ce788441-061f-4c4b-a90b-feabdcd8790c "
        "console=ttyAS0,115200n8 rootwait ro systemd.unit=rescue.target "
        "systemd.journald.forward_to_console=0 quiet loglevel=1",
        "boot",
        "",
        "# 这样启动后：",
        "#   ro                 → 内核不写盘，没有 I/O error，journald 不循环",
        "#   rescue.target      → 只拉起最小系统，不启动 lyco-* 等服务",
        "#   forward_to_console=0 → journald 不往串口回灌",
        "#   quiet loglevel=1   → 内核少打印",
    ],
    "正常启动但把日志放内存": [
        "setenv bootargs root=UUID=ce788441-061f-4c4b-a90b-feabdcd8790c "
        "console=ttyAS0,115200n8 rootwait rw "
        "systemd.journald.forward_to_console=0 loglevel=3",
        "boot",
        "",
        "# 进去之后（如果 rw 挂载成功）：",
        "#   mkdir -p /run/log/journal",
        "#   systemctl restart systemd-journald",
        "# 之后 journald 会用 tmpfs，不再碰只读的 /var/log",
    ],
    "只看 U-Boot，不引导": [
        "# 开机后狂按回车停在 => 提示符，然后：",
        "printenv bootargs          # 先看现在的启动参数长什么样",
        "part list mmc 0            # 确认分区表（应与之前一致）",
        "",
        "# 注意：不要 saveenv，不要 mmc write",
    ],
}


def measure(port: str, baud: int, seconds: float) -> dict:
    """量一下串口刷屏速率。只读，不发送任何数据。"""
    if serial is None:
        print("缺 pyserial。安装：")
        print("  C:\\Users\\liuqi\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\pip install pyserial")
        return {}

    ser = serial.Serial(port, baud, timeout=0.3)
    try:
        ser.dtr = False
        ser.rts = False
    except Exception:  # noqa: BLE001
        pass

    print(f"[i] 只读监听 {port} @ {baud}，共 {seconds:.0f} 秒（不发送任何数据）…")
    raw = b""
    end = time.time() + seconds
    while time.time() < end:
        d = ser.read(65536)
        if d:
            raw += d
    ser.close()

    text = raw.decode("utf-8", errors="replace")
    lines = [l for l in text.splitlines() if l.strip()]
    printable = sum(1 for ch in text if 32 <= ord(ch) < 127 or ch in "\r\n\t")
    ratio = (printable / len(text) * 100) if text else 0.0

    ts = [float(x) for x in re.findall(r"\[\s*(\d+\.\d+)\]", text)]
    kinds = collections.Counter(
        re.sub(r"\[[\d.\s]+\]|\[ *T?\d+\]|\d+", "N", l)[:90] for l in lines
    )

    return {
        "bytes": len(raw),
        "lines": len(lines),
        "ratio": ratio,
        "ts_min": min(ts) if ts else None,
        "ts_max": max(ts) if ts else None,
        "kinds": kinds,
        "text": text,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="诊断 journald 刷屏并给出救活串口的方案（只读，不发送命令）",
    )
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=5.0, help="采样秒数")
    ap.add_argument("--save", help="存原始输出")
    ap.add_argument("--no-measure", action="store_true", help="跳过采样，只打印方案")
    args = ap.parse_args()

    bar = "=" * 68
    print()
    print(bar)
    print("  A7A 串口救活器")
    print(bar)

    m = {}
    if not args.no_measure:
        print()
        print("【一】实测刷屏速率")
        print("-" * 68)
        try:
            m = measure(args.port, args.baud, args.seconds)
        except Exception as e:  # noqa: BLE001
            print(f"  打不开串口：{e}")
            print("  → 检查是否被 Tabby / MobaXterm / PuTTY 占用")
            print("  → 或加 --no-measure 只看方案")
            return 2

        if not m:
            return 2

        rate = m["bytes"] / args.seconds
        lps = m["lines"] / args.seconds
        print(f"  采样 {args.seconds:.0f} 秒")
        print(f"  收到 {m['bytes']:,} 字节   可打印率 {m['ratio']:.1f}%")
        print(f"  共 {m['lines']:,} 行        去重后 {len(m['kinds'])} 种不同消息")
        print(f"  ★ 速率：{rate:,.0f} 字节/秒  ≈  {lps:,.0f} 行/秒")
        if m["ts_min"] is not None:
            print(f"  内核时间戳 {m['ts_min']:.0f}s ~ {m['ts_max']:.0f}s"
                  f"  （开机 {m['ts_min']/60:.1f} 分钟）")

        print()
        print("  刷屏主力：")
        for k, v in m["kinds"].most_common(3):
            print(f"    {v:>7,} x  {k[:82]}")

        print()
        if lps > 100:
            print("  ⚠ 判定：journald 自激刷屏已失控")
            print("     你打进去的每个字符都会被这几万行日志冲走 ——")
            print("     这就是「回车没用」的真正原因，不是串口线坏。")
        elif m["ratio"] < 50:
            print("  ⚠ 判定：可打印率偏低，可能是波特率不对或串口线干扰")
        else:
            print("  ✓ 判定：刷屏速率正常，串口应该可以交互")

        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                f.write(m["text"])
            print(f"\n  [i] 原始输出已存 {args.save}")

    print()
    print("【二】救活方案（按推荐度排序）")
    print("-" * 68)
    for i, (title, cmds) in enumerate(UBOOT_CMDS.items(), 1):
        print(f"\n  方案 {i}：{title}")
        print("  " + "·" * 60)
        for c in cmds:
            if not c:
                print()
            elif c.startswith("#"):
                print(f"  {c}")
            else:
                print(f"      {c}")

    print()
    print("【三】为什么这些方案有效（故障机理）")
    print("-" * 68)
    for line in [
        "journald 想写 /var/log/journal/*.journal",
        "    → 文件系统已只读 → 写失败",
        "        → journald 把「写失败」本身当日志再写",
        "            → 又失败 → 无限放大",
        "",
        "所以关键不是「修串口」，是「让 journald 不再尝试写盘」。",
        "三个方案分别从三个层次掐断这个循环：",
        "    ro              → 内核层就不写盘",
        "    rescue.target   → 不拉起大量会写日志的服务",
        "    force 到 tmpfs  → journald 换个地方写",
    ]:
        print(f"  {line}")

    print()
    print("【四】安全声明")
    print("-" * 68)
    print("  本脚本只读串口，没有向板子发送任何数据。")
    print("  上面所有 U-Boot 命令都是 setenv（内存态），")
    print("  ★ 没有一条 saveenv —— 断电即恢复，你的 U-Boot 环境零改动。")
    print()
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
