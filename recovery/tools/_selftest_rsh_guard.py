#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rsh.py 危险命令护栏的回归测试。

## 为什么必须有这个文件

护栏的价值全在「**拦得住真危险 + 放得过真只读**」。
任何一条只读命令被误拦，人就会开始无脑加 `--i-know-what-im-doing`，
护栏随即失效 —— 所以误报比漏报更伤。

## 起因（2026-09-28 实测）

诊断一块正在坏的 SD 卡时，这条**纯只读**命令被拦了：

    journalctl -b -1 -k --no-pager | grep -iE 'EXT4|voltage select|mmc[0-9]|fsck|read-only'

命中原因：引号里的 `|fsck` 被当成「管道后的 fsck 命令」。
README 里承诺的「误报已修」只覆盖了裸 `\\bfsck\\b`，没覆盖 `[;&|]\\s*` 那一半。
修法是「引号内不算命令位置」+ 对 `sh -c '...'` 递归检查补回覆盖度。

跑法：

    python _selftest_rsh_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rsh import check_dangerous          # noqa: E402

# ── 必须拦住（真会改系统状态 / 大量写盘）────────────────────────────
MUST_BLOCK = [
    "reboot",
    "sudo reboot",
    "shutdown -h now",
    "systemctl poweroff",
    # ↓ 单测抓出来的漏报：`reboot` 前面是 `systemctl`，不在「命令起始位置」
    "systemctl reboot",
    "systemctl halt",
    "init 0",
    "init 6",
    "telinit 6",
    "mkfs.ext4 /dev/mmcblk1p3",
    "dd if=/dev/zero of=/dev/mmcblk1 bs=1M",
    "fdisk /dev/mmcblk1",
    "parted /dev/mmcblk1 print",
    "mount -o remount,rw /",
    "e2fsck -fy /dev/mmcblk1p3",
    "fsck.ext4 -y /dev/mmcblk1p3",
    "tune2fs -c 1 /dev/mmcblk1p3",
    "apt install -y foo",
    "apt-get upgrade",
    "sysctl -w vm.dirty_ratio=5",
    "rm -rf /",
    "saveenv",
    "mmc write 0 0x100 10",
    "true; reboot",
    "foo && shutdown -r now",
    # 引号里藏命令 —— 递归检查必须抓到，否则修误报就修出了漏报
    "bash -c 'reboot'",
    'sh -c "dd if=/dev/zero of=/dev/mmcblk1"',
]

# ── 必须放行（纯只读；误拦的代价更高）──────────────────────────────
MUST_PASS = [
    "uptime",
    "cat /proc/cmdline",
    "cat /sys/block/mmcblk1/device/name",
    "dumpe2fs -h /dev/mmcblk1p3",
    "systemctl show systemd-fsck-root.service",
    "journalctl | grep 'fsck-root'",
    "grep -n 'fsck' /usr/share/initramfs-tools/scripts/local",
    # ★ 本次踩到的那条：引号里的 |fsck 不是命令
    "journalctl -b -1 -k --no-pager | grep -iE 'EXT4|voltage select|mmc[0-9]|fsck|read-only' | head -30",
    "tar czf - --exclude='*.pyc' home/radxa | md5sum",
    "ls -la /boot; cat /proc/cmdline",
]


def main() -> int:
    bad = 0

    print("=== 必须拦住（漏报 = 危险）===")
    for c in MUST_BLOCK:
        hits = check_dangerous(c)
        if hits:
            print(f"  OK   {c}   -> {hits[0][0]}")
        else:
            print(f"  FAIL {c}   -> 没拦住！")
            bad += 1

    print()
    print("=== 必须放行（误报 = 护栏失效）===")
    for c in MUST_PASS:
        hits = check_dangerous(c)
        if not hits:
            print(f"  OK   {c}")
        else:
            print(f"  FAIL {c}   -> 误报 {hits[0][0]}")
            bad += 1

    print()
    print(f"拦 {len(MUST_BLOCK)} 条 / 放 {len(MUST_PASS)} 条，失败 {bad} 条")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
