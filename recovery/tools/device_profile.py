#!/usr/bin/env python3
"""device_profile.py —— 通用 SBC / 嵌入式设备抽象层

为什么需要它：
    这套恢复工具最初是为 Radxa Cubie A7A（Allwinner A733）写的，里面到处
    写死了 `mmcblk1`、`ttyAS0`、`COM3`、`/dev/cedar_dev_ve2` 这类 A7A 专属常量。
    但工具本身做的事（验卡、看 MMC 控制器报错、判读写不对称、扫串口、
    修 ext4）在**任何** Linux 设备上都成立 —— 树莓派、Orange Pi、Rock 5、
    RISC-V 板子、甚至一台 x86 小主机。

    这个模块把"设备长什么样"抽出来，让上层工具只关心"要做什么"。

设计原则：
    1. **自动探测优先**：能自己发现就不要用户填。用户填错参数比不给参数更糟。
    2. **回退链条清晰**：探测失败 → 用镜像/发行版默认 → 用通用默认 → 报错并说清原因。
    3. **零依赖**：只用标准库，能在 initramfs、busybox、救援 shell 里跑。
    4. **不假设是哪个厂商**：靠 /proc、/sys、/etc/os-release 这些标准接口判断。

用法：
    from device_profile import DeviceProfile, detect_profile

    p = detect_profile()                 # 自动探测当前设备
    print(p.summary())

    p.root_block        # "mmcblk0" 或 "nvme0n1" 或 "sda"
    p.root_partition    # "/dev/mmcblk0p2"
    p.serial_console    # "ttyAMA0" / "ttyS0" / "ttyAS0" / None
    p.storage_kind      # "sd" | "emmc" | "nvme" | "usb" | "virtio" | "unknown"
    p.mmc_hosts         # [{"dev": "mmcblk0", "name": "mmc@4020000", ...}]

命令行自检：
    python3 device_profile.py            # 人话报告
    python3 device_profile.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ══════════════════════════════════════════════════════════════════════
# 底层工具
# ══════════════════════════════════════════════════════════════════════

def _read(path: str, default: str = "") -> str:
    """读一个小文件，失败返回默认值（不要抛异常 —— 救援环境里文件常常不存在）。"""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    """跑一个命令，失败返回空串。绝不抛异常。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _glob_dirs(pattern: str) -> list[str]:
    import glob
    return sorted(glob.glob(pattern))


# ══════════════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════════════

@dataclass
class MmcHost:
    """一个 MMC/SD 控制器（Linux 侧）。"""
    block_dev: str = ""        # "mmcblk0"
    sysfs_name: str = ""       # "mmc@4020000" 或 "fe2b0000.mmc"
    host_path: str = ""        # /sys/class/mmc_host/mmc0
    kind: str = "unknown"      # "sd" | "emmc" | "sdio" | "unknown"
    name: str = ""             # 卡的 CID name（有的驱动给 ASCII 名）
    manfid: str = ""           # 厂商 ID
    oemid: str = ""
    serial: str = ""
    date: str = ""
    life_time: str = ""
    speed: str = ""
    is_removable: bool = False
    # 内核报错计数（决定这块控制器是不是在闹）
    err_count: int = 0

    @property
    def display(self) -> str:
        tag = self.block_dev or "?"
        if self.sysfs_name:
            return f"{tag} ({self.sysfs_name})"
        return tag


@dataclass
class DeviceProfile:
    # ── 身份 ──
    hostname: str = ""
    model: str = ""
    arch: str = ""
    distro: str = ""
    kernel: str = ""
    dt_model: str = ""         # 设备树里的 model（比 hostname 更接近硬件真相）
    dt_compatible: list[str] = field(default_factory=list)

    # ── 存储 ──
    root_device: str = ""      # "/dev/mmcblk0p2"
    root_block: str = ""       # "mmcblk0"
    root_mount_opts: str = ""
    root_is_readonly: bool = False
    storage_kind: str = "unknown"
    mmc_hosts: list[MmcHost] = field(default_factory=list)
    all_disks: list[dict] = field(default_factory=list)

    # ── 串口 ──
    serial_console: Optional[str] = None   # "ttyAMA0"
    serial_baud: Optional[int] = None
    serial_ports: list[str] = field(default_factory=list)

    # ── 能力设备（不同厂商叫法差别很大，所以统一成"能力"而不是"节点"）──
    npu_nodes: list[str] = field(default_factory=list)
    vpu_nodes: list[str] = field(default_factory=list)     # 替代掉 A7A 专属的 cedar
    gpu_nodes: list[str] = field(default_factory=list)
    dma_heap_nodes: list[str] = field(default_factory=list)

    # ── 诊断元信息 ──
    probe_warnings: list[str] = field(default_factory=list)

    # ── 设备"族群"判定（用于选默认值和规则集）──
    family: str = "generic"    # "allwinner" | "rockchip" | "raspberrypi" | "x86" | "generic"

    # ────────────────────────────────────────────────────────
    def summary(self) -> str:
        L = []
        L.append(f"设备:     {self.model or self.hostname or '(未知)'}")
        if self.dt_model and self.dt_model != self.model:
            L.append(f"DTB model:{self.dt_model}")
        L.append(f"架构:     {self.arch}    内核: {self.kernel}")
        if self.distro:
            L.append(f"发行版:   {self.distro}")
        L.append(f"厂商族:   {self.family}")
        L.append("")
        L.append(f"根文件系统: {self.root_device or '(未探测到)'}  "
                 f"[{self.storage_kind}]{'  ← 只读' if self.root_is_readonly else ''}")
        if self.root_mount_opts:
            L.append(f"挂载选项:   {self.root_mount_opts}")

        if self.mmc_hosts:
            L.append("")
            L.append("MMC/SD 控制器:")
            for h in self.mmc_hosts:
                extra = []
                if h.kind != "unknown":
                    extra.append(h.kind)
                if h.is_removable:
                    extra.append("可插拔")
                if h.name:
                    extra.append(f"name={h.name}")
                if h.life_time:
                    extra.append(f"life={h.life_time}")
                if h.err_count:
                    extra.append(f"⚠️ {h.err_count} 条报错")
                suffix = ("  " + " ".join(extra)) if extra else ""
                L.append(f"  {h.display}{suffix}")

        if self.serial_console:
            L.append("")
            L.append(f"串口控制台: {self.serial_console} @ {self.serial_baud or '?'}")
        if self.serial_ports:
            L.append(f"可用串口:   {', '.join(self.serial_ports)}")

        caps = []
        if self.npu_nodes:      caps.append(f"NPU={'/'.join(self.npu_nodes)}")
        if self.vpu_nodes:      caps.append(f"VPU={'/'.join(self.vpu_nodes)}")
        if self.gpu_nodes:      caps.append(f"GPU={'/'.join(self.gpu_nodes)}")
        if caps:
            L.append("")
            L.append("加速器:     " + "  ".join(caps))

        if self.probe_warnings:
            L.append("")
            L.append("探测提示:")
            for w in self.probe_warnings:
                L.append(f"  ⚠️  {w}")

        return "\n".join(L)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ══════════════════════════════════════════════════════════════════════
# 探测：身份
# ══════════════════════════════════════════════════════════════════════

def detect_identity(p: DeviceProfile) -> None:
    p.hostname = _read("/proc/sys/kernel/hostname") or os.environ.get("HOSTNAME", "")
    # 注意：os.uname() 在 Windows 上不存在，必须先试命令再回退到 platform
    if hasattr(os, "uname"):
        p.arch = os.uname().machine
    else:
        import platform as _plat
        p.arch = _plat.machine()
    if not p.arch:
        p.arch = _run(["uname", "-m"])
    p.kernel = _run(["uname", "-r"])

    # 设备树 model 是最可靠的硬件标识（很多 SBC 的 hostname 是用户随便改的）
    base = "/proc/device-tree"
    p.dt_model = _read(f"{base}/model").rstrip("\x00")
    comp = f"{base}/compatible"
    if os.path.isdir(comp):
        try:
            p.dt_compatible = [
                Path(f"{comp}/{e}").read_text(errors="replace").rstrip("\x00")
                for e in sorted(os.listdir(comp))
            ]
        except OSError:
            pass
    else:
        c = _read(comp).rstrip("\x00")
        if c:
            p.dt_compatible = [c]

    if not p.model:
        p.model = p.dt_model

    # 发行版
    osrel = "/etc/os-release"
    if os.path.exists(osrel):
        for line in _read(osrel).splitlines():
            if line.startswith("PRETTY_NAME="):
                p.distro = line.split("=", 1)[1].strip().strip('"')
                break


def detect_family(p: DeviceProfile) -> None:
    """判定设备"族群" —— 只用于挑默认值和规则集，不做任何硬性判断。"""
    blob = " ".join([p.dt_model] + p.dt_compatible + [p.arch]).lower()

    if any(k in blob for k in ("allwinner", "sun50i", "sun55i", "sun60i", "sun8i", "a733", "h616", "h618")):
        p.family = "allwinner"
    elif any(k in blob for k in ("rockchip", "rk35", "rk33", "rk3588", "rk3568", "rk3566")):
        p.family = "rockchip"
    elif any(k in blob for k in ("raspberrypi", "bcm27", "bcm28")):
        p.family = "raspberrypi"
    elif any(k in blob for k in ("x86", "amd64", "intel", "i686")):
        p.family = "x86"
    elif any(k in blob for k in ("amlogic", "meson")):
        p.family = "amlogic"
    elif any(k in blob for k in ("mediatek", "mt8", "mt6")):
        p.family = "mediatek"
    elif any(k in blob for k in ("qualcomm", "qcom", "sdm", "sm8")):
        p.family = "qualcomm"
    else:
        p.family = "generic"


# ══════════════════════════════════════════════════════════════════════
# 探测：存储
# ══════════════════════════════════════════════════════════════════════

def _block_kind(dev: str) -> str:
    """从块设备名推断介质类型。"""
    if dev.startswith("mmcblk"):
        return "sd"       # 可能是 sd 也可能是 emmc，后面用 /sys 细分
    if dev.startswith("nvme"):
        return "nvme"
    if dev.startswith("sd"):
        return "usb"      # 也可能是 SATA；看 /sys 里的 transport 更准
    if dev.startswith("vd"):
        return "virtio"
    if dev.startswith("hd"):
        return "sata"
    return "unknown"


def _refine_kind_from_sysfs(dev: str, kind: str) -> str:
    """用 /sys 把推测变精确（尤其是 sd vs emmc）。"""
    if kind == "usb":
        # /sys/block/sda/removable = 1 → U 盘/读卡器；0 → 可能是 SATA
        rem = _read(f"/sys/block/{dev}/removable")
        if rem == "0":
            # 看它挂在什么总线上
            link = ""
            try:
                link = os.readlink(f"/sys/block/{dev}").lower()
            except OSError:
                pass
            if "nvme" in link:
                return "nvme"
            if "ata" in link or "sata" in link:
                return "sata"
        return "usb"
    return kind


def detect_storage(p: DeviceProfile) -> None:
    # ── 根文件系统 ──
    src = _run(["findmnt", "-nro", "SOURCE", "/"])
    if not src:
        # findmnt 可能不存在（busybox / initramfs），退回 /proc/mounts
        for line in _read("/proc/mounts").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "/":
                src = parts[0]
                break

    if src:
        p.root_device = src
        base = os.path.basename(src)
        # mmcblk0p2 → mmcblk0 ; nvme0n1p3 → nvme0n1 ; sda2 → sda
        m = re.match(r"^(mmcblk\d+|nvme\d+n\d+|sd[a-z]+|vd[a-z]+|hd[a-z]+)", base)
        if m:
            p.root_block = m.group(1)
        else:
            p.root_block = base

    opts = _run(["findmnt", "-nro", "OPTIONS", "/"])
    if not opts:
        for line in _read("/proc/mounts").splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1] == "/":
                opts = parts[3]
                break
    p.root_mount_opts = opts
    p.root_is_readonly = bool(re.search(r"(^|,)ro(,|$)", opts))

    if p.root_block:
        k = _block_kind(p.root_block)
        p.storage_kind = _refine_kind_from_sysfs(p.root_block, k)
        # mmcblk 还要细分 sd / emmc
        if k == "sd":
            p.storage_kind = _mmc_kind(p.root_block)

    # ── 列出所有块设备 ──
    for name in sorted(os.listdir("/sys/block")) if os.path.isdir("/sys/block") else []:
        if name.startswith(("loop", "ram", "zram", "sr", "dm-", "md")):
            continue
        size = _read(f"/sys/block/{name}/size")
        secs = int(size) if size.isdigit() else 0
        p.all_disks.append({
            "dev": name,
            "size_gib": round(secs * 512 / 1024**3, 2),
            "removable": _read(f"/sys/block/{name}/removable") == "1",
            "kind": _block_kind(name),
            "is_root": name == p.root_block,
        })

    # ── MMC 控制器（这块板子历史上出问题的地方）──
    detect_mmc_hosts(p)


def _mmc_kind(block_dev: str) -> str:
    """用 /sys 判断 mmcblkX 是 SD 卡还是 eMMC。"""
    # /sys/block/mmcblk0 → .../mmc_host/mmc0/mmc0:0001/type
    try:
        real = os.path.realpath(f"/sys/block/{block_dev}")
    except OSError:
        return "sd"
    # 往上找 mmc_host
    cur = real
    for _ in range(6):
        cur = os.path.dirname(cur)
        if os.path.basename(cur).startswith("mmc"):
            # 找同级的 mmcX:YYYY 目录
            try:
                for e in os.listdir(cur):
                    if re.match(r"^mmc\d+:[0-9a-f]+$", e):
                        t = _read(f"{cur}/{e}/type")
                        # type: 0=MMC 1=SD 2=SDIO 3=SDcombo
                        return {"0": "emmc", "1": "sd", "2": "sdio",
                                "3": "sd"}.get(t, "sd")
            except OSError:
                pass
            break
    # 回退：removable=1 一定是可插拔的（SD），否则倾向 eMMC
    return "sd" if _read(f"/sys/block/{block_dev}/removable") == "1" else "emmc"


def detect_mmc_hosts(p: DeviceProfile) -> None:
    """枚举 /sys/class/mmc_host/* —— 这是跨厂商通用接口。"""
    host_root = "/sys/class/mmc_host"
    if not os.path.isdir(host_root):
        return

    # 先统计 dmesg 里每个 host 的报错数，用于标记"哪个通道在闹"
    dmesg = _run(["dmesg"], timeout=8.0)
    if not dmesg:
        # 有些系统限制非 root 读 dmesg
        dmesg = _read("/var/log/dmesg")

    for host in sorted(os.listdir(host_root)):
        hp = f"{host_root}/{host}"
        h = MmcHost(host_path=hp)

        # 控制器的 sysfs 名字（如 mmc@4020000 或 fe2b0000.mmc）
        try:
            devlink = os.readlink(f"{hp}/device")
            h.sysfs_name = os.path.basename(devlink)
        except OSError:
            h.sysfs_name = host

        # 找到挂在它下面的块设备与卡信息
        try:
            entries = [e for e in os.listdir(hp) if re.match(r"^mmc\d+:[0-9a-f]+$", e)]
        except OSError:
            entries = []

        for e in entries:
            ep = f"{hp}/{e}"
            h.name = h.name or _read(f"{ep}/name")
            h.manfid = h.manfid or _read(f"{ep}/manfid")
            h.oemid = h.oemid or _read(f"{ep}/oemid")
            h.serial = h.serial or _read(f"{ep}/serial")
            h.date = h.date or _read(f"{ep}/date")
            t = _read(f"{ep}/type")
            h.kind = {"0": "emmc", "1": "sd", "2": "sdio", "3": "sd"}.get(t, "unknown")
            h.is_removable = _read(f"{ep}/removable") == "1" or h.kind == "sd"

            # 对应块设备
            try:
                for b in os.listdir(f"{ep}/block"):
                    h.block_dev = b
                    h.life_time = _read(f"/sys/block/{b}/device/life_time")
                    h.speed = _read(f"/sys/block/{b}/device/speed")
                    break
            except OSError:
                pass

        # 这个 host 在内核日志里的报错次数
        if h.sysfs_name:
            h.err_count = dmesg.count(h.sysfs_name)

        # 只收有卡的，或明确存在的 host（避免一堆空通道污染报告）
        if h.block_dev or h.kind != "unknown" or h.err_count:
            p.mmc_hosts.append(h)
        elif entries:
            p.mmc_hosts.append(h)

    if not p.mmc_hosts and os.path.isdir(host_root):
        p.probe_warnings.append("/sys/class/mmc_host 存在但没有可用条目（可能需要 root）")


# ══════════════════════════════════════════════════════════════════════
# 探测：串口
# ══════════════════════════════════════════════════════════════════════

# 常见串口控制台设备名（跨厂商）
_COMMON_CONSOLE_NAMES = [
    "ttyAMA0",   # ARM PL011（树莓派等）
    "ttyS0",     # 标准 8250 / 多数 SoC
    "ttyAS0",    # Allwinner
    "ttyFIQ0",   # Rockchip
    "ttyMV0",    # Marvell
    "ttyPS0",    # Xilinx Zynq
    "ttySC0",    # Renesas
    "ttyMSM0",   # Qualcomm
    "ttymxc0",   # NXP i.MX
    "ttyO0",     # TI OMAP
    "ttyLP0",    # NXP Layerscape
]


def detect_serial(p: DeviceProfile) -> None:
    # 所有存在的串口
    ttys = _glob_dirs("/dev/tty[A-Za-z]*[0-9]")
    p.serial_ports = sorted(os.path.basename(t) for t in ttys
                            if not os.path.basename(t).startswith(("ttyprintk",)))

    # 从 cmdline 里找 console=
    cmdline = _read("/proc/cmdline")
    for tok in cmdline.split():
        if tok.startswith("console="):
            val = tok.split("=", 1)[1]
            parts = val.split(",")
            name = parts[0]
            # 跳过 tty1 / tty0 这类虚拟控制台
            if re.match(r"^tty[0-9]+$", name):
                continue
            p.serial_console = name
            for x in parts[1:]:
                if x.isdigit():
                    p.serial_baud = int(x)
                    break
            break

    # cmdline 里没有就猜
    if not p.serial_console and p.serial_ports:
        for cand in _COMMON_CONSOLE_NAMES:
            if cand in p.serial_ports:
                p.serial_console = cand
                break


# ══════════════════════════════════════════════════════════════════════
# 探测：加速器（不同厂商节点名完全不同，所以是"多模式匹配"）
# ══════════════════════════════════════════════════════════════════════

_NPU_PATTERNS = [
    "/dev/vipcore",          # VeriSilicon VIP（Allwinner/NXP 等）
    "/dev/galcore",          # Vivante
    "/dev/rknpu",            # Rockchip
    "/dev/dri/renderD128",   # 部分 SoC 走 DRM（仅作参考）
    "/dev/amlogic_npu",
]
_NPU_GLOBS = ["/dev/vip*", "/dev/rknpu*", "/dev/npu*"]

_VPU_PATTERNS = [
    "/dev/cedar_dev",        # Allwinner Cedar
    "/dev/cedar_dev_ve2",
    "/dev/mpp_service",      # Rockchip
    "/dev/rkvdec",           # Rockchip
    "/dev/video_dec",
    "/dev/video_enc",
]
_VPU_GLOBS = ["/dev/cedar*", "/dev/rockchip-*", "/dev/video-dec*", "/dev/vpu*"]

_GPU_PATTERNS = ["/dev/dri/card0", "/dev/dri/renderD128", "/dev/mali0",
                 "/dev/pvr_sync", "/dev/kgsl-3d0", "/dev/dxg"]


def _collect(patterns: list[str], globs: list[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        if os.path.exists(pat):
            out.append(pat)
    if globs:
        import glob as _g
        for g in globs:
            for hit in _g.glob(g):
                if hit not in out:
                    out.append(hit)
    return out


def detect_accelerators(p: DeviceProfile) -> None:
    p.npu_nodes = _collect(_NPU_PATTERNS, _NPU_GLOBS)
    p.vpu_nodes = _collect(_VPU_PATTERNS, _VPU_GLOBS)
    p.gpu_nodes = _collect(_GPU_PATTERNS, ["/dev/dri/*"])

    # renderD128 可能被 NPU 和 GPU 同时算进去，去重：GPU 优先
    for n in list(p.npu_nodes):
        if n.startswith("/dev/dri/"):
            p.npu_nodes.remove(n)

    if os.path.isdir("/dev/dma_heap"):
        try:
            p.dma_heap_nodes = [f"/dev/dma_heap/{e}" for e in sorted(os.listdir("/dev/dma_heap"))]
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════
# 对外入口
# ══════════════════════════════════════════════════════════════════════

def detect_profile() -> DeviceProfile:
    p = DeviceProfile()
    detect_identity(p)
    detect_family(p)
    detect_storage(p)
    detect_serial(p)
    detect_accelerators(p)
    return p


# ══════════════════════════════════════════════════════════════════════
# 给上层工具用的便捷函数
# ══════════════════════════════════════════════════════════════════════

def mmc_block_for(target: str = "root") -> Optional[str]:
    """返回 MMC 块设备名，供构造 /sys/block/<dev>/device/... 路径用。

    target:
        "root"  —— 根文件系统所在的那块（最常用）
        "sd"    —— 可插拔 SD 卡
        "emmc"  —— 板载 eMMC
    """
    p = detect_profile()
    if target == "root":
        if p.root_block and p.root_block.startswith("mmcblk"):
            return p.root_block
        # 根不在 MMC 上（比如 NVMe），那就返回第一块 MMC
        for h in p.mmc_hosts:
            if h.block_dev:
                return h.block_dev
        return None
    for h in p.mmc_hosts:
        if h.kind == target and h.block_dev:
            return h.block_dev
    return None


def resolve_mmc_arg(user_value: Optional[str], target: str = "root") -> Optional[str]:
    """把用户给的 --mmc/--dev 参数归一化成块设备名。

    接受：mmcblk1 / /dev/mmcblk1 / mmcblk1p3 / /dev/mmcblk1p3
    None 或 "auto" → 自动探测
    """
    if not user_value or user_value == "auto":
        return mmc_block_for(target)
    base = os.path.basename(user_value)
    m = re.match(r"^(mmcblk\d+|nvme\d+n\d+|sd[a-z]+|vd[a-z]+|hd[a-z]+)", base)
    return m.group(1) if m else base


def resolve_root_partition(user_value: Optional[str] = None) -> Optional[str]:
    """返回根文件系统分区设备路径（如 /dev/mmcblk0p2）。"""
    if user_value:
        return user_value if user_value.startswith("/dev/") else f"/dev/{user_value}"
    p = detect_profile()
    return p.root_device or None


# ══════════════════════════════════════════════════════════════════════
# CLI 自检
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="通用 SBC/嵌入式设备探测 —— 这套恢复工具的底座",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 device_profile.py             # 人话报告
  python3 device_profile.py --json      # 机器可读，供其它脚本消费
""")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    p = detect_profile()

    if args.json:
        print(json.dumps(p.to_dict(), ensure_ascii=False, indent=2))
    else:
        print()
        print("通用设备探测报告")
        print("─" * 56)
        print(p.summary())
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
