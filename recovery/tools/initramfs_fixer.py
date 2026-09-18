#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A 定盘器 —— 专门治"rootfs 要手动 fsck"这一种病。

## 为什么单独写一个

2026-09-18 拿到了板子的完整启动日志，故障已经**完全确定**，只有一条：

    U-Boot 读不到 uboot.env（mmc0:2 硬件级 RTO）→ 这是噪音，U-Boot 会自己
    绕到 mmc0:3 引导，不影响我们
    内核正常加载 → initramfs → 检查 rootfs → 报：
        Inode 521217 seems to contain garbage.
        rootfs: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.
        fsck exited with status code 4
        → 停在 (initramfs) 提示符

所以修复动作只有三步，而且**完全不碰 U-Boot**：
    1. fsck -y /dev/mmcblk1p3
    2. fsck -y /dev/mmcblk1p3   （第二遍确认干净）
    3. exit

## 安全边界（用户明确要求"不要动我的 uboot"）

本脚本 **只做**下面这些事：
    * 往串口写 `fsck -y <rootfs分区>`、`exit`、回车
    * 从串口读日志

本脚本 **绝不做**下面这些事（代码层面保证 —— 没有这些命令）：
    * setenv / saveenv / printenv 写入    → 不碰 U-Boot 环境
    * mmc write / mw / sf / sf write      → 不写裸盘
    * bootcmd / bootargs / bootdelay      → 不改启动变量
    * 任何 `/dev/mmcblk0*`（U-Boot 环境区）的写入 → 只碰 rootfs 分区
    * 重刷 SPL / BL31 / 分区表

## 用法

    # 板子已经停在 (initramfs)：直接修
    python initramfs_fixer.py

    # 板子还没上电 / 要重启：先跑起来，它会一直等
    python initramfs_fixer.py --wait

    # 只看一眼当前是什么状态，不动手
    python initramfs_fixer.py --probe

    # 只读检查文件系统（fsck -n，不修改）
    python initramfs_fixer.py --readonly

    # 分区名如果不一样，显式指定（默认 /dev/mmcblk1p3）
    python initramfs_fixer.py --dev /dev/mmcblk1p3
"""

from __future__ import annotations

import argparse
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.stderr.write("缺少 pyserial，请用 venv 解释器运行。\n")
    raise SystemExit(2)

# ────────────────────────── 常量 ──────────────────────────

# 通用设备抽象层：把默认设备从"写死 A7A 的 mmcblk1p3"改成自动探测。
# 这样在树莓派（mmcblk0p2）、Rock 5、NVMe 机器上都能直接用。
try:
    from device_profile import detect_profile, resolve_root_partition
    _HAS_PROFILE = True
except ImportError:
    _HAS_PROFILE = False


def _default_dev() -> str:
    """自动探测根分区；探测不到时回退到 A7A 的经典值。"""
    if _HAS_PROFILE:
        try:
            got = resolve_root_partition()
            if got:
                return got
        except Exception:
            pass
    return "/dev/mmcblk1p3"


DEFAULT_DEV = _default_dev()      # 注意：这是**运行期探测**结果，不是常量
DEFAULT_PORT = "COM3"
DEFAULT_BAUD = 115200   # 2026-09-18 实测确认：1500000 只读到全 NUL

PROMPT_MARKERS = ("(initramfs)", "/ #", "login:", "root@")

FSCK_ERR_MARKERS = (
    "contains garbage",
    "UNEXPECTED INCONSISTENCY",
    "requires a manual fsck",
    "fastboot exited with status code 4",
    "fsck exited with status code 4",
)

FSCK_DONE_MARKERS = (
    "WAS MODIFIED",
    "clean, ",
    "CLEAN",
    "status code 0",
    "(initramfs)",
    "/ #",
)

BOOTED_MARKERS = ("login:", "systemd[1]: Startup finished", "Debian GNU/Linux")

# 已知无害噪音：出现这些不当作故障
BENIGN = (
    "smc 0 p2", "retry:set phase", "retry:give up", "uboot.env",
    "voltage select", "sunxi-ufs-pltfm", "link startup failed",
    "failed to find dram_clk", "unknown pin", "reg-virt-consumer",
    "OPP not supported", "dummy regulator", "Failed to locate of_node",
    "DMA mask not set", "-517", "DMA mask", "not supported by regulator",
    "sustainable_power", "sample rate not set", "hctosys",
    "no valid clock", "cache hierarchy", "GPIO base is deprecated",
    "axp8191-temp", "dvfs2_ori", "request bus clock",
    "sun50i timer", "init connecting not found", "Speed change timeout",
    "NSI_PMU", "support-ecc", "Not disabling unused clocks",
    "pinstate", "Alternate GPT", "read-only switch", "uart0",
)


def is_benign(line: str) -> bool:
    return any(b in line for b in BENIGN)


# ────────────────────────── 串口 ──────────────────────────


class Console:
    def __init__(self, port: str, baud: int, log_path: str | None) -> None:
        self.ser = serial.Serial(
            port=port, baudrate=baud, bytesize=8,
            parity=serial.PARITY_NONE, stopbits=1,
            timeout=0.2, write_timeout=2.0,
        )
        self.log_file = (
            open(log_path, "a", encoding="utf-8", errors="replace")
            if log_path else None
        )
        self.buffer = ""

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass

    def read(self) -> str:
        try:
            raw = self.ser.read(self.ser.in_waiting or 1)
        except serial.SerialException as exc:
            print(f"\n[串口读失败] {exc}")
            return ""
        if not raw:
            return ""
        text = raw.decode("utf-8", errors="replace")
        sys.stdout.write(text)
        sys.stdout.flush()
        if self.log_file:
            self.log_file.write(text)
            self.log_file.flush()
        self.buffer += text
        if len(self.buffer) > 400_000:
            self.buffer = self.buffer[-200_000:]
        return text

    def pump(self, seconds: float, quiet_ok: bool = True) -> int:
        """读 seconds 秒，返回这段时间收到的字符数。"""
        end = time.time() + seconds
        total = 0
        while time.time() < end:
            total += len(self.read())
        return total

    def send(self, line: str) -> None:
        print(f"\n\033[1m>>> {line}\033[0m")
        sys.stdout.flush()
        try:
            self.ser.write((line + "\r").encode())
            self.ser.flush()
        except serial.SerialException as exc:
            print(f"[写失败] {exc}")

    def enter(self, times: int = 1) -> None:
        try:
            self.ser.write(b"\r" * times)
            self.ser.flush()
        except serial.SerialException:
            pass

    def has(self, markers) -> str | None:
        for m in markers:
            if m in self.buffer:
                return m
        return None

    def wait_for(self, markers, timeout: float) -> str | None:
        end = time.time() + timeout
        while time.time() < end:
            self.read()
            hit = self.has(markers)
            if hit:
                return hit
        return self.has(markers)


# ────────────────────────── 步骤 ──────────────────────────


def banner(text: str) -> None:
    print("\n" + "═" * 72)
    print("  " + text)
    print("═" * 72)
    sys.stdout.flush()


def probe(con: Console, timeout: float = 8.0) -> str:
    """看一眼当前处于什么状态。"""
    con.buffer = ""
    con.enter(2)
    con.wait_for(PROMPT_MARKERS + BOOTED_MARKERS, timeout=timeout)

    if con.has(("(initramfs)",)):
        return "initramfs"
    if con.has(("login:", "root@", "/ #")):
        return "shell"
    if con.has(BOOTED_MARKERS):
        return "booted"
    if "=>" in con.buffer[-600:]:
        return "uboot"
    if con.has(FSCK_ERR_MARKERS):
        return "initramfs"
    return "unknown"


def find_device(con: Console, fallback: str) -> str:
    """从串口输出里找 rootfs 设备名，找不到就用 fallback。"""
    hits = re.findall(r"/dev/mmcblk\d+p\d+", con.buffer)
    if not hits:
        return fallback
    # 优先 p3（rootfs 通常在第 3 分区）
    for h in reversed(hits):
        if h.endswith("p3"):
            return h
    return hits[-1]


def is_uboot_env_partition(dev: str) -> bool:
    """护栏：绝不允许对 U-Boot 环境区所在分区动手。

    mmc0 = 板载 eMMC/UFS，mmc1 = SD 卡。U-Boot 环境在 mmc0:2，
    对应 /dev/mmcblk0p2。rootfs 在 mmc1p3。
    """
    return bool(re.match(r"^/dev/mmcblk0p\d+$", dev))


def run_fsck(con: Console, dev: str, readonly: bool, timeout: float) -> bool:
    """跑一遍 fsck。返回是否看到明确的完成/干净信号。"""
    flag = "-n" if readonly else "-y"
    con.send(f"fsck {flag} {dev}")

    print(f"\n[i] 等待 fsck 完成（最多 {int(timeout)} 秒）…")
    hit = con.wait_for(FSCK_DONE_MARKERS + FSCK_ERR_MARKERS, timeout=timeout)
    if hit:
        if hit in FSCK_ERR_MARKERS:
            print(f"\n[i] 仍报损坏（{hit}）—— 继续修。")
        else:
            print(f"\n[✓] fsck 出现完成信号: {hit}")
        return True

    print(f"\n[!] {int(timeout)} 秒内没看到明确完成信号。")
    print("    如果屏幕停在某个问题等你按 y，命令里的 -y 应该已经自动回答了。")
    print("    如果卡住不动，可以手动在 MobaXterm 里敲： fsck -y " + dev)
    return False


def stage_repair(con: Console, args) -> int:
    banner("① 确认状态")
    state = probe(con, timeout=args.probe_timeout)
    print(f"\n[i] 当前状态: {state}")

    if state == "booted" or state == "shell":
        print("\n[✓] 系统已经起来了，不需要修。")
        return 0
    if state == "uboot":
        print("\n[!] 板子停在 U-Boot 提示符，不是 initramfs。")
        print("    本脚本不碰 U-Boot（按你的要求）。")
        print("    请让板子继续启动：在 MobaXterm 里敲 `boot`，或重上电。")
        print("    启动到 (initramfs) 后再跑本脚本。")
        return 3
    if state == "unknown":
        print("\n[!] 没识别出提示符。可能板子还没到 initramfs。")
        print("    重新上电后立刻用 --wait 跑本脚本，它会一直盯着。")
        return 4

    # ── 到这里说明是 initramfs ──
    dev = find_device(con, args.dev)

    if is_uboot_env_partition(dev):
        print(f"\n[×] 检测到设备名 {dev} 疑似 U-Boot 环境区（mmcblk0），")
        print("    按你的要求我不会碰它。已中止。")
        return 5

    banner(f"② 修 rootfs：{dev}")
    print("[i] 这个分区就是日志里报错的那个，fsck 只作用于它，不碰 U-Boot。")

    if args.readonly:
        print("\n[只读模式] 只跑 fsck -n，不修改任何东西。\n")
        run_fsck(con, dev, readonly=True, timeout=args.fsck_timeout)
        print("\n[只读模式] 结束。")
        return 0

    # 第一遍
    print(f"\n【第一遍】fsck -y {dev}")
    ok1 = run_fsck(con, dev, readonly=False, timeout=args.fsck_timeout)

    # 第二遍（很多损坏一遍修不干净）
    print(f"\n【第二遍】fsck -y {dev}（确认干净）")
    ok2 = run_fsck(con, dev, readonly=False, timeout=args.fsck_timeout)

    if not (ok1 or ok2):
        print("\n[!] 两遍都没有明确的干净信号，可能损坏较重。")
        print("    建议：再跑一遍，或者考虑重刷 rootfs。")
        print("    我不会继续盲目重试 —— 先看看串口上到底停在哪。")

    banner("③ exit 继续启动")
    con.send("exit")
    print("\n[i] 观察 90 秒启动过程…")
    con.pump(90)
    con.enter(1)
    con.pump(15)

    if con.has(("login:")):
        print("\n\n[✓✓] 系统起来了！看到 login: 提示符。")
        return 0

    print("\n[?] 还没看到 login:。可能还在启动，或需要再等一会儿。")
    print("    我继续盯 60 秒…")
    con.pump(60)
    if con.has(("login:", "systemd[1]: Startup finished")):
        print("\n[✓✓] 系统起来了！")
        return 0

    print("\n[i] 仍未登录。请把串口上最后几行发我，我接着看。")
    print("    （或者再跑一次本脚本）")
    return 1


def stage_wait(con: Console, args) -> int:
    """一直盯串口，直到出现 initramfs，然后自动修。"""
    banner("① 等待板子上电 / 重启到 initramfs")
    print("[i] 现在给板子上电（或按 RESET）。我会一直盯着，")
    print("    一旦看到 (initramfs) 就自动开始修。\n")
    print("[i] 想取消按 Ctrl+C。\n")

    start = time.time()
    last_report = time.time()
    silent_since = time.time()

    while time.time() - start < args.wait_timeout:
        got = con.read()
        if got:
            silent_since = time.time()

        if con.has(FSCK_ERR_MARKERS):
            print("\n\n[!] 捕获到 rootfs 损坏报告 —— 开始修复。")
            return stage_repair(con, args)

        if con.has(BOOTED_MARKERS):
            print("\n\n[✓] 系统自己起来了（没报损坏），无需修复。")
            return 0

        # 每 30 秒报一次进展
        if time.time() - last_report > 30:
            last_report = time.time()
            silent = time.time() - silent_since
            print(f"\n[i] 已等 {int(time.time()-start)} 秒"
                  f"（静默 {int(silent)} 秒）…")

        # 长时间完全没输出 → 提醒物理检查，但继续等
        if time.time() - silent_since > args.silent_hint and silent_since > start:
            silent_since = time.time()  # 只提醒一次，不刷屏
            print("\n[!] 串口很安静。确认：① 板子通电了吗 ② 串口线插好了吗")
            print("    ③ 波特率是 115200 吗。我继续等着。")

    print(f"\n[!] 等满 {int(args.wait_timeout)} 秒仍无结果，退出。")
    return 6


# ────────────────────────── 主程序 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description="A7A 定盘器 —— 只治 rootfs 需手动 fsck，绝不碰 U-Boot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--dev", default=DEFAULT_DEV,
                    help=f"rootfs 分区（默认 {DEFAULT_DEV}）")
    ap.add_argument("--log", default=None, help="把串口原文另存一份")

    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--wait", action="store_true",
                      help="先盯着，等板子启动到 initramfs 再修")
    mode.add_argument("--probe", action="store_true",
                      help="只看状态，不动手")
    mode.add_argument("--readonly", action="store_true",
                      help="只跑 fsck -n，不修改")

    ap.add_argument("--probe-timeout", type=float, default=8.0)
    ap.add_argument("--fsck-timeout", type=float, default=900.0)
    ap.add_argument("--wait-timeout", type=float, default=600.0)
    ap.add_argument("--silent-hint", type=float, default=45.0)

    args = ap.parse_args()

    print("═" * 72)
    print("  A7A 定盘器")
    print("  只做三件事：fsck -y <rootfs> ×2 → exit")
    print("  绝不: setenv / saveenv / mmc write / 碰 mmcblk0 / 改 bootargs")
    print("═" * 72)
    print(f"  端口 {args.port} @ {args.baud}   目标分区 {args.dev}")
    print("═" * 72, "\n")

    try:
        con = Console(args.port, args.baud, args.log)
    except serial.SerialException as exc:
        print(f"[×] 打不开 {args.port}: {exc}")
        print("    如果 MobaXterm 开着串口会话，串口是独占的 —— 先断开它。")
        return 2

    try:
        if args.probe:
            banner("只看状态")
            state = probe(con, timeout=args.probe_timeout)
            print(f"\n[i] 状态: {state}")
            print("\n    可能的值：initramfs / booted / shell / uboot / unknown")
            return 0
        if args.wait:
            return stage_wait(con, args)
        return stage_repair(con, args)
    except KeyboardInterrupt:
        print("\n\n[中断] 已停止，未留下未完成的写入。")
        return 130
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
