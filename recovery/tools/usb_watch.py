#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
usb_watch.py — 实时监听 Windows 的 USB / 磁盘插拔事件。

用途：判断「读卡器插上后，Windows 到底有没有反应」。
      分三种结果：
        A. 完全没有事件   → 物理链路没通（线/口/槽/供电）
        B. 有 USB 事件但无磁盘 → 读卡器芯片被认到，但卡没认到（卡槽问题/卡接触）
        C. 有磁盘事件     → 认到了，记下盘符/型号，可以进一步读 CID

这个脚本是只读的：只订阅系统事件，不碰任何硬件，不写盘。

用法：
    python usb_watch.py            # 监听 90 秒
    python usb_watch.py 180        # 监听 180 秒
    python usb_watch.py 0          # 一直监听，Ctrl-C 退出
"""

import ctypes
import ctypes.wintypes as wt
import re
import sys
import time

# ---------- Windows 事件日志读取（用 wevtutil，避免额外依赖） ----------

import subprocess
import json


def run_ps(script: str, timeout: int = 30) -> str:
    """执行 PowerShell 脚本并返回 stdout。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=timeout,
        )
        out = r.stdout.decode("utf-8", errors="replace")
        if not out.strip():
            out = r.stdout.decode("gbk", errors="replace")
        return out
    except Exception as e:
        return f"<PS 调用失败: {e}>"


def snapshot() -> dict:
    """当前磁盘快照：盘号 -> (型号, 大小, 总线类型)"""
    ps = (
        "$d = Get-Disk | ForEach-Object { "
        "'{0}|{1}|{2}|{3}' -f $_.Number,$_.FriendlyName,$_.Size,$_.BusType }; "
        "$d -join \"`n\""
    )
    out = run_ps(ps)
    disks = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            disks[parts[0]] = (parts[1], parts[2], parts[3])
    return disks


def usb_storage_present() -> list:
    """当前存在的 USBSTOR / 可移动磁盘设备"""
    ps = (
        "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object {$_.InstanceId -like 'USBSTOR*'} | "
        "ForEach-Object { '{0}|{1}' -f $_.Status,$_.InstanceId }; "
        "$r = @(); $r -join \"`n\""
    )
    return [l.strip() for l in run_ps(ps).splitlines() if l.strip()]


def recent_usb_events(minutes: int = 3) -> list:
    """最近 N 分钟内 System 日志里的 USB/磁盘相关事件"""
    ps = (
        f"$s=(Get-Date).AddMinutes(-{minutes}); "
        "Get-WinEvent -FilterHashtable @{LogName='System';StartTime=$s} "
        "-ErrorAction SilentlyContinue | "
        "Where-Object {$_.ProviderName -match 'USB|Disk|storahci|stornvme|sdstor|uaspstor|partmgr|volmgr'} | "
        "Select-Object -First 60 | "
        "ForEach-Object { '{0}|{1}|{2}|{3}' -f "
        "$_.TimeCreated.ToString('HH:mm:ss'),$_.Id,$_.ProviderName,"
        "(($_.Message -split \"`n\")[0]) }; "
        "$o = @(); $o -join \"`n\""
    )
    return [l.strip() for l in run_ps(ps).splitlines() if l.strip()]


def main():
    dur = 90
    if len(sys.argv) > 1:
        try:
            dur = int(sys.argv[1])
        except ValueError:
            pass

    print("=" * 68)
    print("  USB 插拔实时监听")
    print("=" * 68)
    print()
    print("  请现在就插上读卡器（或拔掉再插上），我会实时报告。")
    print()

    base_disks = snapshot()
    base_usbstor = usb_storage_present()

    print(f"  基线：磁盘 {len(base_disks)} 个")
    for k, v in base_disks.items():
        print(f"          #{k}  {v[0]}  {int(v[1])/1e9:.1f}GB  {v[2]}")
    print(f"  基线：USBSTOR 设备 {len(base_usbstor)} 个（在线）")
    for u in base_usbstor:
        print(f"          {u}")
    print()
    print("  --- 开始监听，插拔读卡器 ---")
    print()

    t0 = time.time()
    seen = set()
    found_disk = False
    found_usb = False
    tick = 0

    try:
        while True:
            if dur > 0 and time.time() - t0 > dur:
                break
            tick += 1

            cur_disks = snapshot()
            new_disks = {k: v for k, v in cur_disks.items() if k not in base_disks}
            if new_disks:
                found_disk = True
                for k, v in new_disks.items():
                    print(f"  [{(time.time()-t0):5.1f}s] ★★★ 新磁盘出现！"
                          f"#{k}  {v[0]}  {int(v[1])/1e9:.1f}GB  {v[2]}")

            cur_usb = usb_storage_present()
            new_usb = [u for u in cur_usb if u not in base_usbstor]
            if new_usb:
                found_usb = True
                for u in new_usb:
                    if u not in seen:
                        seen.add(u)
                        print(f"  [{(time.time()-t0):5.1f}s] ★★  USB 存储设备出现：{u}")

            if tick % 10 == 0:
                evs = recent_usb_events(1)
                for e in evs:
                    if e not in seen:
                        seen.add(e)
                        print(f"  [{(time.time()-t0):5.1f}s] （系统日志）{e}")

            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  （手动中断）")

    print()
    print("=" * 68)
    print("  结果判定")
    print("=" * 68)
    if found_disk:
        print("  ✔ C. 认到了新磁盘 —— 可以直接读卡。")
        print("     下一步：")
        print("       python tools/sd_verify.py --list")
    elif found_usb:
        print("  △ B. 读卡器芯片被认到，但磁盘没出现。")
        print("     说明：读卡器本身通了，但里面的 SD 卡没被识别。")
        print("     可能：卡没插到底 / 卡槽坏 / 卡本身接触不良。")
        print("     下一步：换一张确定的好卡插同一个读卡器试试。")
    else:
        print("  ✘ A. 完全没有反应。物理链路没通。")
        print("     说明：Windows 连电信号都没收到。")
        print("     按顺序试：")
        print("       1) 换扩展坞上的另一个 USB 口（特别是 USB 3.0 蓝色口）")
        print("       2) 拔掉扩展坞，读卡器直插电脑本体 USB 口")
        print("       3) 换一根 USB 线（如果是带线的读卡器）")
        print("       4) 换一个读卡器")
    print()

    print("  附：最近 3 分钟系统 USB/磁盘事件")
    evs = recent_usb_events(3)
    if evs:
        for e in evs[:30]:
            print(f"    {e}")
    else:
        print("    （无 —— 这本身就说明系统没察觉到任何插拔）")
    print()


if __name__ == "__main__":
    main()
