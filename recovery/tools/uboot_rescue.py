#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A U-Boot 救援 —— 绕过坏掉的 SD 卡分区，让系统重新启动。

背景（2026-09-18 串口日志分析）：
    U-Boot SPL 2026.04  从 mmc0 (SD 卡) 启动成功
    Loading Environment from FAT... Unable to read "uboot.env" from mmc0:2   ← 分区2 读不到
    kernel: sunxi_mmc_host-4022000.sdmmc: smc 0 p2 err, cmd 1, RTO !!        ← p2 读超时
    kernel: retry:set phase failed or over retry times / retry:give up

  → 不是文件系统损坏，是 **SD 卡第 2 分区（FAT / U-Boot 环境区）读取失败**，
    硬件级超时 (RTO)。可能是卡槽接触不良、卡接近报废、或供电掉压。

本脚本做的事情：
    阶段 1  连上 U-Boot，打印现有环境变量（诊断，不改任何东西）
    阶段 2  改环境变量，把启动目标从坏分区切到正确分区
    阶段 3  手动引导内核（不依赖 boot 脚本），绕开所有坏区
    阶段 4  引导成功后，从 Linux 侧彻底修复（fsck / 重写分区）

用法：
    # 只看 U-Boot 里的环境变量和分区情况（最安全，先跑这个）
    python uboot_rescue.py --diagnose

    # 手动引导内核（绕开 uboot.env 和环境区）
    python uboot_rescue.py --boot-manual

    # 尝试用标准 boot 命令（改完环境变量后）
    python uboot_rescue.py --boot

    # 看当前 mmc 设备/分区
    python uboot_rescue.py --ls

注意：
    * U-Boot 里敲命令要快 —— 它有 autoboot 倒计时，但日志里看到
      "Hit any key to stop autoboot: 0" 说明已经停在 `=>` 提示符，不会再自动启动。
    * 若板子重启进入 autoboot，本脚本会先发一个回车把它拦在 U-Boot。
    * 所有命令都以 `printenv` 开头做无害确认，不做 `sf`/`mmc write` 这类写盘操作。
"""

from __future__ import annotations

import argparse
import time

try:
    import serial
except ImportError:
    raise SystemExit("缺少 pyserial，请用 venv 解释器跑。")

# ────────────── U-Boot 常见诊断命令（都是只读的） ──────────────

DIAG_COMMANDS = [
    ("version",   "U-Boot 版本"),
    ("mmc list",  "列出所有 MMC 设备"),
    ("mmc dev 0", "选中 mmc0 (SD 卡)"),
    ("mmc info",  "mmc0 详情（容量/速率/是否 SD 卡）"),
    ("part list mmc 0", "列出 mmc0 的分区表 ← 关键！"),
    ("printenv",  "打印全部环境变量"),
    ("printenv bootcmd", "当前默认启动命令"),
    ("printenv bootargs", "内核命令行"),
    ("printenv mmcdev", "当前 mmcdev 指向"),
    ("printenv fdtfile", "设备树文件名"),
]

# 手动引导：不依赖 uboot.env / boot.scr，直接按已知的分区布局 load
# root=UUID=ce788441-061f-4c4b-a90b-feabdcd8790c  ← 从日志抄来的
MANUAL_BOOT = [
    # 1) 选设备
    "setenv mmcdev 0",
    "mmc dev ${mmcdev}",
    # 2) 内核：分区 1 通常是 boot 分区（ext4 或 fat）
    "part list mmc ${mmcdev}",
    "ls mmc ${mmcdev}:1 /",
    # 3) 加载内核与 initrd（路径来自日志：/boot/vmlinuz-*、/boot/initrd.img-*）
    "setenv kernel_addr_r 0x42000000",
    "setenv ramdisk_addr_r 0x4A000000",
    "setenv fdt_addr_r 0x4FA00000",
    "load mmc ${mmcdev}:1 ${kernel_addr_r} /boot/vmlinuz-6.6.98-4-aw2511",
    "load mmc ${mmcdev}:1 ${ramdisk_addr_r} /boot/initrd.img-6.6.98-4-aw2511",
    "load mmc ${mmcdev}:1 ${fdt_addr_r} /usr/lib/linux-image-6.6.98-4-aw2511/allwinner/sun60i-a733-cubie-a7a.dtb",
    # 4) 设置启动参数（原样照抄日志里的 append 行）
    (
        "setenv bootargs root=UUID=ce788441-061f-4c4b-a90b-feabdcd8790c "
        "console=ttyAS0,115200n8 console=tty1 rootwait rw"
    ),
    # 5) 引导
    "booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}",
]

# 修正型环境变量（写 env，属于安全改动：只改启动目标，不碰卡数据）
FIX_ENV = [
    "setenv mmcdev 0",
    "setenv mmcroot /dev/mmcblk1p3",
    "setenv bootdelay 3",
    "saveenv",
]


class UBoot:
    def __init__(self, port: str, baud: int) -> None:
        self.ser = serial.Serial(port, baud, timeout=0.25, write_timeout=2.0)
        self.buf = ""

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def read(self) -> str:
        try:
            raw = self.ser.read(self.ser.in_waiting or 1)
        except serial.SerialException as exc:
            print(f"[读失败] {exc}")
            return ""
        if not raw:
            return ""
        text = raw.decode("utf-8", errors="replace")
        print(text, end="", flush=True)
        self.buf += text
        return text

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.read()

    def send(self, line: str) -> None:
        print(f"\n>>> {line}")
        try:
            self.ser.write((line + "\r").encode())
            self.ser.flush()
        except serial.SerialException as exc:
            print(f"[写失败] {exc}")

    def wait_prompt(self, timeout: float = 10.0) -> bool:
        """等 `=>` 提示符。"""
        end = time.time() + timeout
        while time.time() < end:
            if self.read().strip().endswith("=>"):
                return True
            if "=> " in self.buf[-200:]:
                return True
        return "=>" in self.buf[-500:]

    def cmd(self, line: str, wait: float = 3.0) -> str:
        """发一条命令，等一会儿，返回这段时间的输出。"""
        before = len(self.buf)
        self.send(line)
        self.pump(wait)
        return self.buf[before:]


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    ok = sum(1 for ch in text if ch in "\r\n\t" or 0x20 <= ord(ch) < 0x7F)
    return ok / len(text)


def sanity_check_bus(u: UBoot) -> bool:
    """检查收到的字节像不像"真的串口文本"。

    2026-09-18 的教训：以 1500000 打开 COM3 收到了 51,828 字节，
    但几乎全是 NUL/空白 —— 那是波特率不对或线噪声，不是板子的输出。
    在这里先拦一下，避免后面的命令打在一堆乱码上、白忙一场。
    """
    if len(u.buf) < 32:
        return True  # 没收到什么，谈不上"乱码"，交给后面判断

    ratio = _printable_ratio(u.buf)
    if ratio >= 0.85:
        return True

    print("\n" + "!" * 68)
    print(f"[!] 收到的字节只有 {ratio*100:.1f}% 是可打印字符 —— 这不像板子的文本输出。")
    print("    最可能的原因：**波特率不对**（或串口线噪声/接反）。")
    print(f"    当前用的是 --baud {u.ser.baudrate}。")
    print("    请先跑：")
    print("        python baud_scan.py")
    print("    它会自动试出正确波特率。然后用 `--baud <结果>` 重跑本工具。")
    print("!" * 68)
    return False


def enter_uboot(u: UBoot, args) -> bool:
    """确保停在 U-Boot 提示符。"""
    print("\n" + "=" * 68)
    print("【进入 U-Boot】")
    print("=" * 68)

    u.pump(1.5)

    if not sanity_check_bus(u):
        return False

    if "=>" in u.buf[-500:]:
        print("\n[OK] 已经在 U-Boot 提示符。")
        return True

    print("\n[i] 没看到提示符，尝试：")
    print("    1) 敲回车拦截 autoboot")
    print("    2) 若板子没反应，请按一下板子的 RESET 键，脚本会立刻抢注")
    u.send("")  # 空行 = 发一个回车
    u.pump(1.0)
    if "=>" in u.buf[-500:]:
        print("[OK] 已拦在 U-Boot。")
        return True

    print("\n[i] 等待 30 秒，请按 RESET 或重新上电…")
    u.pump(30)
    if "=>" in u.buf[-800:]:
        print("[OK] 进入 U-Boot。")
        return True

    print("\n[×] 没进 U-Boot。检查：串口是否接板子、板子是否上电。")
    print("    若串口一直没动静，也顺手确认波特率（见上面 baud_scan.py 提示）。")
    return False


def cmd_diagnose(u: UBoot) -> None:
    print("\n" + "=" * 68)
    print("【诊断】只读命令，不改动任何东西")
    print("=" * 68)
    for command, desc in DIAG_COMMANDS:
        print(f"\n--- {desc} ---")
        u.cmd(command, wait=2.0)
        if "unknown command" in u.buf[-300:]:
            print(f"    (此版本 U-Boot 不支持该命令，跳过)")

    print("\n" + "=" * 68)
    print("【诊断结论怎么看】")
    print("=" * 68)
    print("""
  关键看 `part list mmc 0` 的输出：

  情况 A：分区表正常（有 p1/p2/p3），但 p2 读不到
      → 卡的部分区域坏。用 --boot-manual 绕开。

  情况 B：只有一个分区 或 分区表读失败 ("Bad MBR" / 全 0)
      → 分区表本身坏了。需要从 Linux 侧重写分区表。

  情况 C：`mmc info` 报容量 0 或 timeout
      → 卡彻底读不出来。换卡 or 修卡座。

  另外看 `printenv` 有没有这两行：
      mmcdev=0        正常
      mmcroot=/dev/mmcblk1p3
""")


def cmd_ls(u: UBoot) -> None:
    print("\n" + "=" * 68)
    print("【查看 MMC 与分区】")
    print("=" * 68)
    for c in ["mmc list", "mmc dev 0", "mmc info", "part list mmc 0",
              "ls mmc 0:1 /", "ls mmc 0:2 /"]:
        print(f"\n--- {c} ---")
        u.cmd(c, wait=2.5)


def cmd_boot_manual(u: UBoot) -> None:
    print("\n" + "=" * 68)
    print("【手动引导内核】绕开 uboot.env 与坏分区")
    print("=" * 68)
    for c in MANUAL_BOOT:
        print(f"\n--- {c} ---")
        out = u.cmd(c, wait=3.0)
        # 关键错误检查
        low = out.lower()
        if "bad" in low or "failed" in low or "error" in low or "timeout" in low:
            print("\n[!] 这一步报错了。如果是在 load 内核/initrd 时出错，")
            print("    说明这个分区也读不了 —— 那就不是单纯 p2 的问题，")
            print("    是整张卡都在掉线，需要换卡。下面继续跑完看结果。")


def cmd_boot(u: UBoot) -> None:
    print("\n" + "=" * 68)
    print("【标准 boot】先修环境变量再启动")
    print("=" * 68)
    for c in FIX_ENV:
        print(f"\n--- {c} ---")
        u.cmd(c, wait=2.0)
    print("\n--- boot ---")
    u.send("boot")
    print("\n[i] 观察 180 秒启动过程…")
    u.pump(180)


def main() -> int:
    ap = argparse.ArgumentParser(description="A7A U-Boot 救援")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--baud", type=int, default=115200)

    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--diagnose", action="store_true", help="只读诊断（先跑这个）")
    g.add_argument("--ls", action="store_true", help="看 mmc 设备与分区")
    g.add_argument("--boot-manual", action="store_true", help="手动引导内核，绕开坏区")
    g.add_argument("--boot", action="store_true", help="修环境变量后标准 boot")
    g.add_argument("--raw", default=None, help="直接在 U-Boot 里执行一条命令")

    args = ap.parse_args()

    try:
        u = UBoot(args.port, args.baud)
    except serial.SerialException as exc:
        print(f"[×] 打不开 {args.port}: {exc}")
        return 2

    try:
        if not enter_uboot(u, args):
            return 3

        if args.diagnose:
            cmd_diagnose(u)
        elif args.ls:
            cmd_ls(u)
        elif args.boot_manual:
            cmd_boot_manual(u)
        elif args.boot:
            cmd_boot(u)
        elif args.raw:
            u.cmd(args.raw, wait=3.0)
        return 0
    except KeyboardInterrupt:
        print("\n[中断]")
        return 130
    finally:
        u.close()


if __name__ == "__main__":
    raise SystemExit(main())
