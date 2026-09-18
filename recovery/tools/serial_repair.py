#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A 串口修复助手 —— 在 Windows 侧通过 FTDI 串口接管板子的 initramfs 控制台。

用途：
  A7A 的 rootfs 损坏（Inode ... seems to contain garbage / fsck exited with status 4），
  启动卡在 initramfs 的 (initramfs) 提示符。板子此时没有网络，只能走串口。

  本脚本把"读日志 → 等提示符 → 敲 fsck → 等修完 → exit 重启"整套自动化，
  并在每一步打印人话进度，避免用户对着黑屏不知道发生了什么。

用法：
  # 1) 只监听，看板子现在到底在什么状态（不改任何东西，最安全）
  python serial_repair.py --port COM3 --watch

  # 2) 真修（先确认 --watch 看到 (initramfs) 提示符了再用）
  python serial_repair.py --port COM3 --fix

  # 3) 只读检查（不改盘），等价于 fsck -n
  python serial_repair.py --port COM3 --check-only

参数：
  --port      串口名（Windows: COM3；Linux: /dev/ttyUSB0）
  --baud      波特率，默认 115200（2026-09-18 实测确认，1500000 读到的是全 NUL）
  --watch     只监听并打印，不发送任何按键
  --fix       执行 fsck -y <dev> 然后 exit 继续启动
  --check-only 执行 fsck -n <dev>（只读，不修改），然后回显结果
  --dev       根分区设备，默认从日志里自动抓；抓不到则用 /dev/mmcblk1p3
  --log       把完整原始串口输出另存一份到文件
  --timeout   等待提示符的最长秒数，默认 180

注意：
  * 串口是独占资源，跑之前请确认没有别的工具（MobaXterm/minicom/PuTTY）占着。
  * 若提示 Permission/拒绝访问，说明端口被占用，先关掉其他串口软件。
  * 本脚本不会猜测分区名去乱修 —— 只修日志里明确报出来的那个设备。
"""

from __future__ import annotations

import argparse
import re
import sys
import time

try:
    import serial  # pyserial
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "缺少 pyserial。请用带 pyserial 的解释器运行，例如：\n"
        "  C:/Users/liuqi/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe "
        "serial_repair.py ...\n"
    )
    raise SystemExit(2)

# 板上日志里出现这些就说明已经到 initramfs 提示符了
PROMPT_MARKERS = ("(initramfs)", "initramfs)")

# 需要人工 fsck 的典型报错
FSCK_NEEDED_MARKERS = (
    "contains garbage",
    "UNEXPECTED INCONSISTENCY",
    "requires a manual fsck",
    "fsck exited with status code 4",
)

# 从日志抓根分区设备：形如
#   The root filesystem on /dev/mmcblk1p3 requires a manual fsck
#   /dev/mmcblk1p3: UNEXPECTED INCONSISTENCY
DEV_RE = re.compile(r"(/dev/(?:mmcblk\d+p\d+|sd[a-z]\d+|nvme\d+n\d+p\d+))")

# 不需要修的"已正常启动"信号
BOOTED_MARKERS = (
    "login:",
    "Debian GNU/Linux",
    "systemd[1]: Startup finished",
)


class Console:
    """串口控制台的最小封装：单向读 + 可写整行。"""

    def __init__(self, port: str, baud: int, log_path: str | None = None) -> None:
        self.ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.2,
            write_timeout=2.0,
        )
        self.log_file = open(log_path, "a", encoding="utf-8", errors="replace") if log_path else None
        self.buffer = ""  # 累积的可读文本（用于关键词匹配）

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
        if self.log_file:
            self.log_file.close()

    def read_chunk(self) -> str:
        """读一小段，解成文本后同时打到屏幕和日志文件。"""
        try:
            raw = self.ser.read(self.ser.in_waiting or 1)
        except serial.SerialException as exc:
            sys.stderr.write(f"\n[串口读失败] {exc}\n")
            return ""
        if not raw:
            return ""
        # 板上输出一般 UTF-8；混入非法字节时不要崩（这是实测踩过的坑）
        text = raw.decode("utf-8", errors="replace")
        sys.stdout.write(text)
        sys.stdout.flush()
        if self.log_file:
            self.log_file.write(text)
            self.log_file.flush()
        self.buffer += text
        # 防止 buffer 无限增长
        if len(self.buffer) > 200_000:
            self.buffer = self.buffer[-100_000:]
        return text

    def send_line(self, line: str, echo: bool = True) -> None:
        """发送一整行（自动补 \\r）。"""
        if echo:
            sys.stdout.write(f"\n>>> {line}\n")
            sys.stdout.flush()
        payload = (line + "\r").encode("utf-8")
        try:
            self.ser.write(payload)
            self.ser.flush()
        except serial.SerialException as exc:
            sys.stderr.write(f"\n[串口写失败] {exc}\n")

    def send_enter(self, times: int = 1) -> None:
        """敲回车唤醒提示符（initramfs 有时需要几次回车才回显）。"""
        for _ in range(times):
            try:
                self.ser.write(b"\r")
                self.ser.flush()
            except serial.SerialException:
                return
            time.sleep(0.4)

    def pump(self, seconds: float) -> str:
        """持续读指定秒数，返回这段时间内新读到的文本。"""
        got = ""
        deadline = time.time() + seconds
        while time.time() < deadline:
            got += self.read_chunk()
        return got

    def wait_for_any(self, markers, timeout: float, keep_reading: bool = True) -> str | None:
        """等到 buffer 里出现任一 marker，返回命中的那个；超时返回 None。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.read_chunk()
            for m in markers:
                if m in self.buffer:
                    return m
            if not keep_reading:
                time.sleep(0.1)
        return None


def detect_device(text: str, fallback: str) -> str:
    """从日志文本里抓根分区设备名。"""
    hits = DEV_RE.findall(text)
    if not hits:
        return fallback
    # 优先带 p3 的（rootfs），否则取最后一个
    for h in hits:
        if h.endswith("p3"):
            return h
    return hits[-1]


def cmd_watch(con: Console, args) -> int:
    print("\n" + "=" * 68)
    print("【监听模式】只读，不发送任何按键。按 Ctrl+C 退出。")
    print("=" * 68 + "\n")
    start = time.time()
    reported = set()
    try:
        while time.time() - start < args.timeout:
            text = con.read_chunk()
            if not text:
                continue
            # 一旦发现"需要人工 fsck"，明确告诉用户看到了什么
            for m in FSCK_NEEDED_MARKERS:
                if m in text and m not in reported:
                    reported.add(m)
                    print(f"\n\n[!] 检测到: {m}")
            for m in PROMPT_MARKERS:
                if m in text and "prompt" not in reported:
                    reported.add("prompt")
                    print("\n\n[OK] 已经到 initramfs 提示符 —— 可以跑 --fix 了\n")
            for m in BOOTED_MARKERS:
                if m in text and "booted" not in reported:
                    reported.add("booted")
                    print(f"\n\n[OK] 已经正常启动到系统（看到 {m!r}），不需要修盘。\n")
    except KeyboardInterrupt:
        print("\n\n[退出监听]")
    dev = detect_device(con.buffer, args.dev)
    print(f"\n--- 汇总 ---")
    print(f"  识别到的根分区设备 : {dev}")
    print(f"  是否看到 fsck 报错 : {'是' if any(m in con.buffer for m in FSCK_NEEDED_MARKERS) else '否'}")
    print(f"  是否看到提示符     : {'是' if any(m in con.buffer for m in PROMPT_MARKERS) else '否'}")
    print(f"  是否已正常启动     : {'是' if any(m in con.buffer for m in BOOTED_MARKERS) else '否'}")
    return 0


def run_fsck(con: Console, dev: str, readonly: bool, wait_s: float) -> bool:
    """在提示符下执行 fsck，等待完成。返回是否看起来成功。"""
    flag = "-n" if readonly else "-y"
    cmd = f"fsck {flag} {dev}"
    con.send_line(cmd)

    # fsck 大分区可能跑几分钟，等到出现"完成"的特征或超时
    done_markers = (
        "FILE SYSTEM WAS MODIFIED",
        "FILE SYSTEM IS CLEAN",
        "fsck exited with status code 0",
        "$ ",
        "(initramfs)",
    )
    print(f"\n[i] 正在等待 {cmd} 执行完成（最长 {int(wait_s)} 秒）…")
    got = con.wait_for_any(done_markers, timeout=wait_s)
    if got is None:
        print(f"\n[!] {int(wait_s)} 秒内没看到明确的完成标志，下面把已有输出给你看：")
        con.pump(2)
        return False
    print(f"\n[i] 看到完成标志: {got!r}")
    con.pump(2)
    return True


def cmd_fix(con: Console, args) -> int:
    print("\n" + "=" * 68)
    print("【修复模式】将唤醒提示符 → fsck -y → exit 继续启动")
    print("=" * 68 + "\n")

    # 1) 先敲几次回车，看提示符出不出来
    print("[1/4] 敲回车唤醒控制台…")
    for attempt in range(1, 4):
        con.send_enter(1)
        got = con.wait_for_any(PROMPT_MARKERS, timeout=4)
        if got:
            print(f"      [OK] 第 {attempt} 次回车后出现提示符")
            break
    else:
        print("      [!] 回车后没看到 (initramfs) 提示符。")
        print("          继续尝试盲发 fsck（有些固件提示符不回显）。")

    dev = detect_device(con.buffer, args.dev)
    print(f"[i] 目标根分区: {dev}")

    # 2) 先做一次只读检查，把问题亮出来
    print("\n[2/4] 先做只读检查 fsck -n（不改盘，只看有什么问题）…")
    run_fsck(con, dev, readonly=True, wait_s=min(args.timeout, 300))

    # 3) 真修
    print("\n[3/4] 执行修复 fsck -y（会实际改动文件系统）…")
    ok = run_fsck(con, dev, readonly=False, wait_s=args.fsck_timeout)
    if not ok:
        print("\n[!] 修复过程没有正常结束。可能的处理：")
        print("    - 再跑一次，很多时候要连跑两遍才干净")
        print("    - 换只读检查看还剩什么错")
        print("    - 若报 device is in use，改跑: fsck -f -y " + dev)
        return 1

    # 4) 继续启动
    print("\n[4/4] 发送 exit 继续启动…")
    con.send_line("exit")
    print("[i] 开始观察启动过程（90 秒）…\n")
    boot = con.wait_for_any(BOOTED_MARKERS, timeout=90)
    if boot:
        print(f"\n\n[OK] 系统已启动（看到 {boot!r}）")
        print("     接下来可以拔掉串口，回网络 SSH 那边继续部署。")
        return 0
    print("\n\n[?] 90 秒内没看到 login 提示。可能还在启动 / 或还有别的故障。")
    print("     建议再跑一次 --watch 看卡在哪里。")
    return 3


def cmd_check_only(con: Console, args) -> int:
    print("\n" + "=" * 68)
    print("【只读检查】fsck -n，不会修改文件系统")
    print("=" * 68 + "\n")
    con.send_enter(2)
    con.wait_for_any(PROMPT_MARKERS, timeout=6)
    dev = detect_device(con.buffer, args.dev)
    print(f"[i] 目标根分区: {dev}")
    run_fsck(con, dev, readonly=True, wait_s=args.fsck_timeout)
    print("\n[i] 结束。上面输出里 'FILE SYSTEM WAS MODIFIED' 若为只读检查则不应出现；")
    print("    若要真正修复，用 --fix。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="A7A 串口 initramfs 修复助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", default="COM3", help="串口名（默认 COM3）")
    ap.add_argument("--baud", type=int, default=115200, help="波特率（默认 115200）")
    ap.add_argument("--dev", default="/dev/mmcblk1p3", help="根分区设备（默认自动识别）")
    ap.add_argument("--log", default=None, help="把原始串口输出保存到文件")
    ap.add_argument("--timeout", type=float, default=180, help="等待提示符秒数（默认 180）")
    ap.add_argument("--fsck-timeout", type=float, default=900, help="fsck 最长等待秒数（默认 900）")

    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--watch", action="store_true", help="只监听，不发按键（最安全）")
    mode.add_argument("--fix", action="store_true", help="执行 fsck -y 并继续启动")
    mode.add_argument("--check-only", action="store_true", help="只读检查 fsck -n")

    args = ap.parse_args()

    if args.log is None:
        args.log = f"serial-{time.strftime('%Y%m%d-%H%M%S')}.log"

    print(f"[i] 打开串口 {args.port} @ {args.baud} …（日志: {args.log}）")
    try:
        con = Console(args.port, args.baud, args.log)
    except serial.SerialException as exc:
        sys.stderr.write(
            f"\n[×] 打开串口失败: {exc}\n\n"
            "常见原因：\n"
            "  1. 端口被别的软件占用（MobaXterm / PuTTY / minicom / 之前的脚本残留）——先关掉\n"
            "  2. FTDI 线没插 / 没上电 —— 设备管理器里看 COM 口是否在位\n"
            "  3. 端口号不是 COM3 —— 用设备管理器确认实际端口号\n"
        )
        return 2

    try:
        if args.watch:
            return cmd_watch(con, args)
        if args.check_only:
            return cmd_check_only(con, args)
        return cmd_fix(con, args)
    finally:
        con.close()
        print(f"\n[i] 串口已关闭，完整日志在 {args.log}")


if __name__ == "__main__":
    raise SystemExit(main())
