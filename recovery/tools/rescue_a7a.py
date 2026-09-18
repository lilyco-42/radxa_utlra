#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A 全自动抢救器 —— 从"卡在 initramfs"一路修到"SSH 能连上"。

它把整条链路串起来，用户只需要把线插好、板子上电：

  ① 打开串口，盯住启动日志
  ② 判断板子处于哪种状态：
       - 卡在 (initramfs) 且报 filesystem 损坏  → 自动 fsck -y + exit
       - 只有 (initramfs) 提示符，没报损坏      → 自动 exit 试一把
       - 正常启动到 login                       → 直接跳到第 ③ 步
       - 完全没有输出                           → 提示检查供电/线序（不瞎等）
  ③ 等系统起来后扫描局域网找板子（因为 fsck 后 IP 可能变）
  ④ SSH 连上后自动做体检：Cedar 设备、内存、systemd、裸盘空间

用法：
  # 全流程（推荐）：板子上电前就跑起来，它会一直等
  python rescue_a7a.py

  # 只想盯日志，不做任何写入
  python rescue_a7a.py --dry-run

  # 已知 IP，跳过网段扫描
  python rescue_a7a.py --ip 192.168.10.165

  # 换串口/波特率
  python rescue_a7a.py --port COM3 --baud 115200
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import socket
import subprocess
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    sys.stderr.write("缺少 pyserial，请用 venv 里的解释器运行。\n")
    raise SystemExit(2)

# ────────────────────────── 关键词表 ──────────────────────────

PROMPT_MARKERS = ("(initramfs)", "initramfs)")
FSCK_NEEDED = (
    "contains garbage",
    "UNEXPECTED INCONSISTENCY",
    "requires a manual fsck",
    "fsck exited with status code 4",
)
BOOTED_MARKERS = ("login:", "systemd[1]: Startup finished", "Debian GNU/Linux")
HARD_FAIL_MARKERS = (
    "Kernel panic",
    "Unable to mount root fs",
    "^]",
)

# ── 已知无害噪音（2026-09-18 从真实启动日志归纳）────────────────────
# 这块板子的 SD 卡第 2 分区（U-Boot 环境区）硬件级读不出来，会狂刷 RTO。
# 但 U-Boot 会自己绕到 mmc0:3 引导，内核也能起来，所以这些只是噪音。
# 不把它们当故障，否则会误报、误判、干扰状态判定。
BENIGN_NOISE = (
    "smc 0 p2 err",
    "retry:set phase failed",
    "retry:give up",
    "Unable to read \"uboot.env\"",
    "Card did not respond to voltage select",
    "loading out-of-tree module taints kernel",
    "sunxi-ufs-pltfm",
    "link startup failed",
    "failed to find dram_clk",
    "unknown pin",
    "Static allocation of GPIO base",
    "reg-virt-consumer",
    "regulator-virtual-consumer",
    "OPP not supported by regulators",
    "using dummy regulator",
    "supply twi not found",
    "supply spi not found",
    "supply uart not found",
    "supply hci not found",
    "Failed to locate of_node",
    "DMA mask not set",
    "axp8191-temp-ctrl",
    "Fail to read 'dvfs2_ori'",
    "request bus clock failed",
    "sun50i timer of resource get failed",
    "master probe failed with -517",
    "attach fail:-517",
    "init connecting not found",
    "Speed change timeout",
    "hctosys: unable to read the hardware clock",
    "no valid clock/calendar values available",
    "Unable to detect cache hierarchy",
    "NSI_PMU",
    "Get support-ecc failed",
    "sample rate not set",
    "sustainable_power will be estimated",
    "Not disabling unused clocks",
    "No ITS available",
    "Manual set ocr",
    "Cann't get uart0 pinstate",
    "Cann't get pin bias hs pinstate",
    "Alternate GPT is invalid",
    "host does not support reading read-only switch",
)

DEV_RE = re.compile(r"(/dev/(?:mmcblk\d+p\d+|sd[a-z]\d+|nvme\d+n\d+p\d+))")

# ────────────────────────── 串口封装 ──────────────────────────


class Console:
    def __init__(self, port: str, baud: int, log_path: str) -> None:
        self.ser = serial.Serial(
            port=port, baudrate=baud, bytesize=8,
            parity=serial.PARITY_NONE, stopbits=1,
            timeout=0.2, write_timeout=2.0,
        )
        self.log_file = open(log_path, "a", encoding="utf-8", errors="replace")
        self.buffer = ""

    def close(self) -> None:
        for closer in (self.ser.close, self.log_file.close):
            try:
                closer()
            except Exception:
                pass

    def read_chunk(self) -> str:
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
        self.log_file.write(text)
        self.log_file.flush()
        self.buffer += text
        if len(self.buffer) > 300_000:
            self.buffer = self.buffer[-150_000:]
        return text

    def send_line(self, line: str) -> None:
        print(f"\n>>> {line}")
        sys.stdout.flush()
        try:
            self.ser.write((line + "\r").encode())
            self.ser.flush()
        except serial.SerialException as exc:
            print(f"[串口写失败] {exc}")

    def send_enter(self, times: int = 1, gap: float = 0.4) -> None:
        for _ in range(times):
            try:
                self.ser.write(b"\r")
                self.ser.flush()
            except serial.SerialException:
                return
            time.sleep(gap)

    def has(self, markers) -> str | None:
        for m in markers:
            if m in self.buffer:
                return m
        return None

    def wait_for(self, markers, timeout: float) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.read_chunk()
            hit = self.has(markers)
            if hit:
                return hit
        return None


# ────────────────────────── 各阶段动作 ──────────────────────────


def banner(step: str, text: str) -> None:
    print("\n" + "═" * 70)
    print(f"  {step}  {text}")
    print("═" * 70)


def stage1_wait_for_state(con: Console, args) -> str:
    """盯启动日志，判断板子状态。返回 state 字符串。"""
    banner("①", "等待板子输出…（把板子上电；脚本会一直盯着）")
    print("[i] 如果 60 秒内完全没输出，我会提示你先查供电和线序，不会干等。\n")

    start = time.time()
    quiet_since = time.time()
    announced = set()

    while time.time() - start < args.state_timeout:
        got = con.read_chunk()
        if got:
            quiet_since = time.time()

        # 实时播报关键事件
        for m in FSCK_NEEDED:
            if m in got and ("fsck" + m) not in announced:
                announced.add("fsck" + m)
                print(f"\n\n[!] 发现文件系统损坏证据: {m}")
        for m in HARD_FAIL_MARKERS:
            if m in got and ("fail" + m) not in announced:
                announced.add("fail" + m)
                print(f"\n\n[!] 发现严重错误: {m}")

        # 状态判定（优先级：已启动 > 提示符 > 卡死证据）
        done = con.has(BOOTED_MARKERS)
        if done:
            return "booted"
        if con.has(PROMPT_MARKERS) and con.has(FSCK_NEEDED):
            return "initramfs-fsck"
        if con.has(PROMPT_MARKERS):
            # 提示符出来后再给 3 秒，看后面有没有跟 fsck 报错
            con.wait_for(FSCK_NEEDED, timeout=3)
            return "initramfs-fsck" if con.has(FSCK_NEEDED) else "initramfs-only"

        # 长时间完全静默 → 提前退出，别让用户干等
        if time.time() - quiet_since > args.quiet_timeout:
            print(f"\n\n[!] 已经 {int(args.quiet_timeout)} 秒没有任何串口输出。")
            return "silent"

    return "timeout"


def stage2_repair(con: Console, args, dev: str) -> bool:
    """在 initramfs 下修盘。"""
    banner("②", f"修复根文件系统 {dev}")
    print("[i] 先敲回车把提示符叫醒…")
    for _ in range(3):
        con.send_enter(1)
        if con.wait_for(PROMPT_MARKERS, timeout=3):
            break
    else:
        print("[i] 没看到提示符回显，但继续盲发命令（有些固件不回显）。")

    if args.dry_run:
        print("\n[dry-run] 只读检查，不修改：")
        con.send_line(f"fsck -n {dev}")
        con.wait_for(("WAS MODIFIED", "CLEAN", "status code 0", "(initramfs)"), timeout=60)
        print("\n[dry-run] 结束，未做任何修改。")
        return False

    print(f"\n[i] 第一遍 fsck -y {dev}（实际修复，可能要几分钟）…")
    con.send_line(f"fsck -y {dev}")
    if con.wait_for(
        ("WAS MODIFIED", "CLEAN", "status code 0", "(initramfs)"),
        timeout=args.fsck_timeout,
    ) is None:
        print("\n[!] fsck 没有明确的完成信号。")
        print("    若卡在 device is in use，需要在提示符下先 umount 再修。")

    # 连跑两遍，很多损坏一遍修不干净
    print(f"\n[i] 第二遍 fsck -y {dev}（确认干净）…")
    con.send_line(f"fsck -y {dev}")
    con.wait_for(("WAS MODIFIED", "CLEAN", "status code 0", "(initramfs)"), timeout=args.fsck_timeout)

    print("\n[i] 发送 exit 继续启动…")
    con.send_line("exit")
    return True


def stage3_find_board(con: Console, args) -> str | None:
    """边看启动日志边扫局域网，返回板子 IP。"""
    banner("③", "等待系统启动并寻找板子 IP")

    if args.ip:
        candidates = [args.ip]
        print(f"[i] 用指定 IP: {args.ip}")
    else:
        print("[i] 开始扫描局域网（fsck 后 IP 可能变化）…")
        candidates = scan_lan(args.subnet)
        print(f"[i] 发现 {len(candidates)} 台设备: {', '.join(candidates) or '无'}")

    # 一边等启动，一边轮询候选 IP 的 22 端口
    deadline = time.time() + args.boot_timeout
    ssh_ready = None
    while time.time() < deadline:
        con.read_chunk()
        if con.has(BOOTED_MARKERS) and not args.ip:
            # 启动到 login 了，重新扫一次网，此时板子应该在线
            new = scan_lan(args.subnet)
            add = [c for c in new if c not in candidates]
            if add:
                print(f"\n[i] 启动后新出现: {', '.join(add)}")
                candidates.extend(add)

        for ip in candidates:
            if port_open(ip, 22, timeout=0.4):
                ssh_ready = ip
                break
        if ssh_ready:
            break

        if con.has(BOOTED_MARKERS):
            print("\n[OK] 板子已启动到系统。")
            break
        time.sleep(1.5)

    if ssh_ready:
        print(f"\n[OK] SSH 端口已通: {ssh_ready}")
        return ssh_ready

    # SSH 没通，但板子起来了 —— 手动扫一次
    print("\n[i] 没自动找到 SSH，最后再扫一次网…")
    for ip in scan_lan(args.subnet):
        if port_open(ip, 22, timeout=0.5):
            print(f"[OK] 找到 SSH: {ip}")
            return ip
    return None


def stage4_health_check(ip: str, args) -> None:
    """SSH 上去做体检。"""
    banner("④", f"板子体检（{ip}）")

    remote_cmd = (
        "echo '--- 系统 ---'; "
        "grep PRETTY /etc/os-release; uname -r; uptime; "
        "echo '--- 内存 ---'; free -h; "
        "echo '--- 磁盘 ---'; df -h / ; "
        "echo '--- Cedar VE2 设备 ---'; "
        "ls -l /dev/cedar_dev /dev/cedar_dev_ve2 /dev/dma_heap/system 2>&1; "
        "echo '--- VP 资产 ---'; "
        "ls -d ~/vp ~/sau ~/biliup ~/html-video ~/a733-cedarc ~/venv-tts 2>&1; "
        "echo '--- systemd 单元 ---'; "
        "systemctl --user list-timers --all 2>&1 | head -8; "
        "echo '--- 网络 ---'; ip -br a | grep -v lo"
    )

    base = ["ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=8",
            "-o", "BatchMode=yes",
            f"radxa@{ip}", remote_cmd]

    for attempt in range(1, 4):
        print(f"[i] 第 {attempt} 次尝试 SSH…")
        try:
            r = subprocess.run(base, capture_output=True, text=True, timeout=40)
        except subprocess.TimeoutExpired:
            print("    超时，重试…")
            continue
        if r.returncode == 0:
            print(r.stdout)
            print("[OK] 体检完成。")
            return
        print(f"    rc={r.returncode} stderr={r.stderr.strip()[:200]}")
        time.sleep(3)

    print("\n[!] 免密 SSH 不可用（可能需要密码或密钥）。")
    print(f"    请手动执行: ssh radxa@{ip}")
    print("    进去后可以直接跑重建包里的脚本：")
    print("      cd ~ && tar xzf a7a-rebuild.tar.gz && ./a7a-rebuild/deploy-rebuild.sh --check")


# ────────────────────────── 网络小工具 ──────────────────────────


def scan_lan(subnet: str, timeout: float = 0.35) -> list[str]:
    """扫网段里哪些主机在 ARP 缓存/能 ping 通。用并发 ping 快一点。"""
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return []
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > 1024:
        hosts = hosts[:1024]

    # Windows ping：-n 1 -w 毫秒
    procs = []
    for h in hosts:
        try:
            procs.append((h, subprocess.Popen(
                ["ping", "-n", "1", "-w", "300", h],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )))
        except Exception:
            pass

    alive = []
    for h, p in procs:
        try:
            if p.wait(timeout=timeout + 2.5) == 0:
                alive.append(h)
        except subprocess.TimeoutExpired:
            p.kill()
    return alive


def port_open(ip: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# ────────────────────────── 主流程 ──────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description="A7A 全自动抢救器：串口修复 → 找板子 → 体检",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dev", default="/dev/mmcblk1p3")
    ap.add_argument("--ip", default=None, help="已知板子 IP，跳过扫描")
    ap.add_argument("--subnet", default="192.168.10.0/24", help="局域网网段")
    ap.add_argument("--log", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只读，不修改文件系统")
    ap.add_argument("--state-timeout", type=float, default=300, help="等待状态判定秒数")
    ap.add_argument("--quiet-timeout", type=float, default=60, help="完全无输出的容忍秒数")
    ap.add_argument("--boot-timeout", type=float, default=180, help="等启动完成秒数")
    ap.add_argument("--fsck-timeout", type=float, default=900)
    args = ap.parse_args()

    if args.log is None:
        args.log = f"rescue-{time.strftime('%Y%m%d-%H%M%S')}.log"

    print("╔" + "═" * 68 + "╗")
    print("║  A7A 全自动抢救器".ljust(60) + "║")
    print("║  " + ("DRY-RUN 只读模式" if args.dry_run else "实修模式").ljust(66) + "║")
    print("╚" + "═" * 68 + "╝")
    print(f"\n串口 {args.port} @ {args.baud}   日志 {args.log}")

    try:
        con = Console(args.port, args.baud, args.log)
    except serial.SerialException as exc:
        print(f"\n[×] 打不开串口 {args.port}: {exc}")
        print("    1) 关掉 MobaXterm/PuTTY/minicom 等占用串口的软件")
        print("    2) 确认 FTDI 线插好、驱动正常（设备管理器里看 COM 号）")
        return 2

    try:
        state = stage1_wait_for_state(con, args)
        print(f"\n>>> 状态判定: {state}")

        if state == "silent":
            print("\n" + "!" * 70)
            print("串口一个字节都没有，先别急着怪系统 —— 按顺序检查：")
            print("  1) 板子电源灯亮了吗？（供电足不足：5V/3A 以上）")
            print("  2) 串口线 TX/RX 是不是接反了？（TX→RX, RX→TX, GND→GND）")
            print("  3) 串口工具波特率是不是 115200？")
            print("  4) 板子是不是根本没上电 / 卡在更早的阶段")
            print("!" * 70)
            return 4

        if state == "booted":
            print("\n[OK] 板子已经正常启动了，根本不需要修盘。")

        elif state in ("initramfs-fsck", "initramfs-only"):
            dev = DEV_RE.search(con.buffer)
            dev = dev.group(1) if dev else args.dev
            print(f"\n[i] 检测到根分区: {dev}")
            print("[i] 这是 48 小时内第二张卡出问题 —— 修完请务必检查供电。")
            went = stage2_repair(con, args, dev)
            if not went:
                print("\n[i] 未继续启动（dry-run 或修复未完成）。")
                return 0

        elif state == "timeout":
            print("\n[!] 等待超时，进入网络探测阶段，试试板子是不是已经起来了。")

        ip = stage3_find_board(con, args)
        if ip:
            stage4_health_check(ip, args)
            print("\n" + "═" * 70)
            print("  下一步：把重建包推上去")
            print("═" * 70)
            print(f"  scp a7a-rebuild.tar.gz radxa@{ip}:~/")
            print(f"  ssh radxa@{ip} 'tar xzf a7a-rebuild.tar.gz && ./a7a-rebuild/deploy-rebuild.sh --check'")
        else:
            print("\n[!] 没找到板子。看上面的串口日志判断卡在哪一步。")
            return 5
        return 0
    except KeyboardInterrupt:
        print("\n\n[中断] 用户取消。")
        return 130
    finally:
        con.close()
        print(f"\n[i] 串口已关闭。完整日志: {args.log}")


if __name__ == "__main__":
    raise SystemExit(main())
