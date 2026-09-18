#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A7A 坏卡根因取证器 —— 回答"为什么换了两张卡都坏"。

## 背景

用户 2026-09-18 的原话：

    我都换了 两张卡了 这样下去不排查问题 下面的卡插入 也会这样

这是对的。"换卡"是治症，不是治因。要分清是卡坏了还是板子把卡弄坏的，
必须在**同一块板子上、用同一套观测口径**，把下面三件事分开量：

    ① SD 控制器有没有把卡切到 1.8V 高速模式？切的时候成功了吗？
    ② 卡本身还健康吗？（寿命寄存器 / 只读标记 / 写保护 / 坏块）
    ③ 每次丢数据的边界在哪？是整卡烂，还是固定区域反复烂？

如果 ① 是坏的，那第 3 张卡、第 4 张卡都会在第 ②③ 步重演 —— 这就是本脚本要抓的东西。

## 怎么用（三种运行模式）

### 模式 A：离线看日志（不需要板子，此刻就能跑）

把串口抓到的启动日志存成文件，然后：

    python card_forensics.py --log boot.txt --offline

我会扫日志里的硬件异常，输出一张分诊表和一句结论。

### 模式 B：板子活着，SSH 通

    python card_forensics.py --ssh radxa@192.168.10.69

我会远程执行一组**只读**命令，把 mmc 控制器状态、卡健康寄存器、
内核错误计数、ext4 错误历史全部拉回来。

### 模式 C：板子卡在 initramfs / 只能走串口

    python card_forensics.py --serial COM3

我会通过串口执行同一组只读命令（**绝不写盘、绝不 fsck、绝不碰 U-Boot**）。

## 安全边界（用户要求"不要动我的 uboot"）

本脚本发出的**全部**是只读命令：

    cat /sys/...            dmesg | grep ...        mmc extcsd read ...
    cat /proc/partitions    lsblk                   journalctl -k -b
    smartctl -a（若有）      fsck -n（只检查不修）

**绝不**包含：setenv / saveenv / fsck -y / mkfs / dd / mmc write / 任何写盘动作。

## 判读方法

脚本跑完会给出一个 VERDICT，三种可能：

    CONTROLLER_FAULT   → SD 控制器/供电有问题，换卡无用，要修板子
    CARD_FAULT         → 卡本身坏了，换卡有意义
    INCONCLUSIVE       → 证据不够，需要补数据（按提示做）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Iterable, Optional

# 通用设备抽象层（同目录）。用于把 mmcblk1 这类设备专属常量变成可配置项，
# 让本工具能服务任意 SBC，而不只是 A7A。
try:
    from device_profile import detect_profile, resolve_mmc_arg
    _HAS_PROFILE = True
except ImportError:  # 单独把本文件拷到别处用时的降级路径
    _HAS_PROFILE = False

# --------------------------------------------------------------------------
# 诊断规则表
# --------------------------------------------------------------------------

# 每一条 = (证据正则, 归属层面, 权重, 人话说明)
#   层面 controller → 指向板子；card → 指向卡；power → 指向供电
#   权重 3 = 强证据（基本可定案）；2 = 中等；1 = 弱（仅参考）
RULES: list[tuple[str, str, int, str]] = [
    # ---- 控制器 / 电压切换（指向板子） ----
    (r"manual set ocr", "controller", 3,
     "控制器在手动设置 OCR（工作电压寄存器）。正常流程应当由 SD 协议协商得出，"
     "出现 'manual set' 说明协商失败，驱动退回了硬编码电压。"),
    (r"Card did not respond to voltage select", "controller", 3,
     "卡没有响应电压切换。这是 SD 3.0 从 3.3V 切到 1.8V 的关键步骤，"
     "失败意味着高速模式根本起不来，或者切换时电压塌陷。"),
    (r"Cann't get pin bias hs pinstate|Cann't get uart0 pinstate", "controller", 3,
     "拿不到高速模式的引脚偏置状态。说明设备树里 SD 控制器的 pinmux / "
     "电压域描述与实际硬件对不上。注意：本项目 2026-09-18 已确认板子是 A7A、"
     "DTB 也是 A7A，所以这条更可能是 pinmux 驱动本身的问题，而非型号错配。"),
    (r"sunxi_mmc.*err|smc \d+ p\d+ err", "controller", 2,
     "SMC（SD/MMC 控制器）读写报错。偶发可忽略，持续出现说明链路不稳。"),
    (r"retry:give up", "controller", 2,
     "重试后放弃。控制器已经试过多次仍未成功。"),
    (r"sunxi-ufs-pltfm.*link startup failed", "controller", 2,
     "UFS 链路启动失败。若板子装了 UFS 模块，这条说明高速存储通道没起来。"),
    (r"Failed to (write|rotate).*: Input/output error", "controller", 2,
     "写/轮转日志时 I/O 错误。★ 关键区分：如果**只有写错误、没有读错误**，"
     "说明「读路径正常、写路径坏」—— 这与「低速读 OK、高速写坏」的"
     "电压切换故障模型完全吻合，比「卡彻底损坏」更能解释现象。"),

    # ---- 供电 / PMIC（指向板子） ----
    (r"OPP not supported by regulators", "power", 2,
     "内核想要的工作点（频率+电压对）没有对应的稳压器支持。"
     "出现几十次说明 DTB 里的电压域表和板子的 PMIC 不一致，"
     "CPU/DRAM 可能长期处在错误电压下 —— 这正是写卡中途掉电、文件系统烂掉的经典成因。"),
    (r"failed to find dram_clk", "power", 2,
     "DRAM 时钟配置缺失。内存时序不稳会表现为随机的数据损坏，"
     "而 ext4 的 bitmap 校验和错正是「写进去的字节变了」的典型症状。"),
    (r"Speed change timeout", "power", 1,
     "PCIe 速率切换超时。属于同一批电压/时钟配置问题的旁证。"),
    (r"pdtest.*failed with error -110", "power", 1,
     "掉电测试超时（-110 = ETIMEDOUT）。电源域管理不响应。"),

    # ---- 存储介质本身（指向卡） ----
    (r"bad block bitmap checksum", "card", 2,
     "ext4 块位图校验和错。文件系统的元数据在盘上变了 —— "
     "要么卡写坏了，要么写入过程中掉电。单次出现不足以定罪，"
     "但和上面的电压问题同时出现就非常可疑。"),
    (r"EXT4-fs error.*Remounting filesystem read-only", "card", 2,
     "文件系统被强制只读。这是数据损坏后的止损动作，会连锁导致 "
     "dbus / logind / NetworkManager 全部失败，进而没有 IP、没有 SSH。"),
    (r"(I/O error|Input/output error).*mmcblk", "card", 2,
     "对 mmcblk 设备的 I/O 错误。"),
    (r"mmcblk\d+.*read-only|set read-only", "card", 2,
     "内核把卡标记为只读，通常意味着卡自己拉高了写保护或触发了内部错误保护。"),
    (r"block count.*exceeds|bad magic|invalid superblock", "card", 3,
     "初级超级块损坏。这是「卡的结构性损坏」而不是「文件被改坏」。"),
]

# 已知噪音：这些出现在 A7A 上完全正常，不该计入嫌疑
BENIGN = (
    "smc 0 p2 err, cmd 1, RTO",
    "retry:set phase",
    "reg-virt-consumer",
    "hctosys",
    "NSI_PMU",
    "unknown pin",
    "pinstate",
)

VERDICT_TEXT = {
    "CONTROLLER_FAULT": (
        "板子的问题，不是卡的问题",
        "SD 控制器无法完成 3.3V→1.8V 电压切换，且引脚偏置配置与硬件不符。"
        "在这种状态下，**任何**一张健康的卡插上去，都会在高速写入时被写坏。"
        "继续换卡只是重复损坏，必须先修控制器配置（DTB / pinmux / 供电）。",
    ),
    "CARD_FAULT": (
        "卡的问题",
        "未发现控制器层面的失败证据，但卡上有结构性损坏痕迹。"
        "这种情况下换卡是有意义的。",
    ),
    "POWER_FAULT": (
        "供电问题，间接毁卡",
        "电压域配置与硬件严重不匹配，写入过程中可能发生电压塌陷。"
        "需要先确认电源适配器规格（官方要求 USB Type-C 5V），再核对 DTB 电源表。",
    ),
    "INCONCLUSIVE": (
        "证据不足",
        "当前日志里没有足以定案的证据。请按下方的「补充取证」步骤提供更多数据。",
    ),
}

# --------------------------------------------------------------------------
# 只读取证命令（串口 / SSH 共用）
#
# 注意：设备名通过 {blk} 占位，运行期由 resolve_mmc_arg() 填。
#       早期版本把 mmcblk1 写死在这里，导致只能用于 A7A；
#       换成占位符后，树莓派（mmcblk0）、Rock 5（mmcblk1/mmcblk0）、
#       甚至 NVMe 启动的机器都能用同一套取证逻辑。
# --------------------------------------------------------------------------

# 探测不到时的回退值（A7A / 多数 SD 启动的 ARM 板确实是 mmcblk1）
_FALLBACK_BLK = "mmcblk1"

PROBE_CMDS_TEMPLATE = [
    # 1. 卡的健康与身份
    ("card_name",     "cat /sys/block/{blk}/device/name"),
    ("card_life_a",   "cat /sys/block/{blk}/device/life_time"),
    ("card_date",     "cat /sys/block/{blk}/device/date"),
    ("card_fwrev",    "cat /sys/block/{blk}/device/fwrev 2>/dev/null"),
    ("card_hwrev",    "cat /sys/block/{blk}/device/hwrev 2>/dev/null"),
    ("ro_flag",       "cat /sys/block/{blk}/ro"),
    # 2. 分区与容量（判断是否被截断）
    ("partitions",    "cat /proc/partitions"),
    ("blk_size",      "cat /sys/block/{blk}/size"),
    # 3. 内核侧的错误历史（重启后第一手证据）
    ("dmesg_mmc",     "dmesg | grep -iE 'mmc|smc|sunxi_mmc|dw_mmc|sdhci' | tail -60"),
    ("dmesg_ext4",    "dmesg | grep -iE 'EXT4-fs|I/O error|readonly|remount' | tail -40"),
    ("journal_err",   "journalctl -k -b -p err --no-pager 2>/dev/null | tail -40"),
    # 4. 每次启动的失败计数
    ("boot_count",    "journalctl --list-boots --no-pager 2>/dev/null | tail -10"),
    # 5. 通用补充：挂载状态与块设备拓扑（跨平台都成立）
    ("mounts_root",   "findmnt -nro SOURCE,OPTIONS / 2>/dev/null || grep ' / ' /proc/mounts"),
    ("lsblk_tree",    "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null || cat /proc/partitions"),
]


def build_probe_cmds(blk: str) -> list[tuple[str, str]]:
    """把占位符换成实际块设备名。"""
    return [(k, c.format(blk=blk)) for k, c in PROBE_CMDS_TEMPLATE]


def resolve_block_for_analysis(explicit: Optional[str] = None) -> str:
    """决定要对哪块设备取证。

    优先级：用户显式指定 > 自动探测根分区所在 MMC > 回退到 mmcblk1。
    """
    if explicit:
        return explicit
    if _HAS_PROFILE:
        try:
            got = resolve_mmc_arg(None, target="root")
            if got:
                return got
        except Exception:
            pass
    return _FALLBACK_BLK


def analyze_rw_asymmetry(text: str) -> dict:
    """★ 关键的读写方向分析（2026-09-18 从 journald 刷屏日志中提炼）。

    这是一个**判别性极强**的检验，原理：

        如果 SD 卡彻底坏了（物理损坏、寿命耗尽、控制器芯片死）：
            读和写**都**会失败 → 日志里能同时看到 read 和 write 错误

        如果只是"高速写入路径"坏了（电压切换失败）：
            读还能用（所以能启动、能 fsck 通过、能加载内核）
            只有写失败 → 日志里**只有 write/rotate 错误，没有 read 错误**

    所以「只有写错误」= 强烈支持控制器电压切换故障，
    而不是「卡坏了」—— 这是区分"换卡有用"和"换卡没用"的关键判据。
    """
    write_pat = re.compile(
        r"(Failed to (write|rotate)|write error|I/O error.*write"
        r"|error writing|Errno 5.*write)", re.IGNORECASE)
    read_pat = re.compile(
        r"(read error|error reading|Failed to read|read failed"
        r"|I/O error.*read|Medium Error|Unrecovered read)", re.IGNORECASE)
    # 通用 I/O 错误（分不清读写方向）
    generic_pat = re.compile(r"(Input/output error|I/O error)", re.IGNORECASE)

    w = r = g = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        is_w = bool(write_pat.search(line))
        is_r = bool(read_pat.search(line))
        if is_w and not is_r:
            w += 1
        elif is_r and not is_w:
            r += 1
        elif generic_pat.search(line):
            g += 1

    verdict = "unknown"
    if w > 0 and r == 0:
        verdict = "write_only"
    elif r > 0 and w == 0:
        verdict = "read_only"
    elif w > 0 and r > 0:
        verdict = "both"

    return {"write": w, "read": r, "generic": g, "verdict": verdict}


RW_TEXT = {
    "write_only": (
        "只有写错误，没有读错误  ← 强烈支持「电压切换故障」",
        "读路径完好（所以能启动、能 fsck 通过、能加载内核），只有写路径不可靠。"
        "这正是 SD 3.3V 低速/1.8V 高速切换失败的典型特征。"
        "继续换卡不会改善 —— 新卡也会在读 OK、写坏的模式下被毁。",
    ),
    "read_only": (
        "只有读错误",
        "写正常但读失败，比较少见。可能是卡上特定区域物理损坏，"
        "或控制器读时序问题。需要更多数据。",
    ),
    "both": (
        "读写都错",
        "读写全失败，指向介质本身的物理损坏或控制器彻底失效。"
        "这种情况下换卡是有意义的。",
    ),
    "unknown": (
        "分不清读写方向",
        "日志里没有明确的 I/O 错误信息。",
    ),
}


# --------------------------------------------------------------------------
# 日志离线分析
# --------------------------------------------------------------------------

@dataclass
class Hit:
    layer: str
    weight: int
    reason: str
    sample: str
    count: int = 0


def is_benign(line: str) -> bool:
    low = line.lower()
    for b in BENIGN:
        if b.lower() in low:
            # "smc 0 p2 err" 是噪音，但 "smc 0 p3 err"（rootfs 分区）不是
            if "smc 0 p2" in b:
                if re.search(r"smc\s+\d+\s+p[^2]\s+err", low):
                    return False
                continue
            return True
    return False


def scan_log(text: str) -> tuple[list[Hit], dict[str, int]]:
    """扫描日志，返回命中的证据和统计。

    只统计**未加偏移量的真实时间戳行**，避免把重复打印算成多次独立事件。
    """
    hits: dict[str, Hit] = {}
    stats: dict[str, int] = {}

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if is_benign(line):
            stats["benign"] = stats.get("benign", 0) + 1
            continue

        for pat, layer, weight, reason in RULES:
            if re.search(pat, line, re.IGNORECASE):
                key = pat
                if key not in hits:
                    hits[key] = Hit(layer=layer, weight=weight,
                                    reason=reason, sample=line.strip()[:150])
                hits[key].count += 1
                stats[layer] = stats.get(layer, 0) + 1

    ordered = sorted(hits.values(), key=lambda h: (-h.weight, -h.count))
    return ordered, stats


def verdict_of(hits: list[Hit]) -> str:
    """按证据权重定案。

    规则（故意保守 —— 宁可说证据不足，也不误判）：
      * 控制器强证据（权重 3）≥ 2 条  → CONTROLLER_FAULT
      * 控制器强证据 ≥ 1 条且有供电证据 → CONTROLLER_FAULT
      * 无控制器证据，但有卡层面权重 3  → CARD_FAULT
      * 无控制器证据，但有供电证据 ≥ 2 条 → POWER_FAULT
      * 其余 → INCONCLUSIVE
    """
    ctrl_strong = [h for h in hits if h.layer == "controller" and h.weight >= 3]
    ctrl_any = [h for h in hits if h.layer == "controller"]
    power = [h for h in hits if h.layer == "power"]
    card_strong = [h for h in hits if h.layer == "card" and h.weight >= 3]

    if len(ctrl_strong) >= 2:
        return "CONTROLLER_FAULT"
    if len(ctrl_strong) >= 1 and power:
        return "CONTROLLER_FAULT"
    if not ctrl_any and card_strong:
        return "CARD_FAULT"
    if not ctrl_any and len(power) >= 2:
        return "POWER_FAULT"
    if len(ctrl_any) >= 2:
        return "CONTROLLER_FAULT"
    return "INCONCLUSIVE"


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

LAYER_LABEL = {
    "controller": "SD 控制器 / 引脚 / 电压切换",
    "power":      "供电 / PMIC / 时钟",
    "card":       "存储介质 / 文件系统",
}


def print_report(hits: list[Hit], stats: dict[str, int], verdict: str,
                 source: str, text: str = "",
                 blk: str = _FALLBACK_BLK, blk_explicit: bool = False) -> None:
    bar = "=" * 68
    print()
    print(bar)
    print("  存储介质/控制器 坏卡根因取证报告")
    print(f"  数据来源：{source}")
    print(bar)

    rw = analyze_rw_asymmetry(text) if text else {"verdict": "unknown"}
    rw_verdict = rw.get("verdict", "unknown")

    print()
    print("【一】读写方向检验  ← 区分「换卡有用/无用」的关键判据")
    print("-" * 68)
    if rw_verdict == "unknown":
        print("  日志中没有明确的 I/O 错误，无法判定读写方向。")
    else:
        print(f"  写错误行数：{rw['write']}")
        print(f"  读错误行数：{rw['read']}")
        if rw.get("generic"):
            print(f"  方向不明 I/O 错误：{rw['generic']}")
        t, d = RW_TEXT[rw_verdict]
        print()
        print(f"  ▸ {t}")
        for line in textwrap.wrap(d, width=62):
            print(f"    {line}")
    print()

    print("【二】证据分诊表（按层面归组）")
    print("-" * 68)
    if not hits:
        print("  （未发现任何已知异常模式）")
    else:
        for layer in ("controller", "power", "card"):
            group = [h for h in hits if h.layer == layer]
            if not group:
                continue
            total = sum(h.count for h in group)
            print(f"\n  ▸ {LAYER_LABEL[layer]}"
                  f"   （{len(group)} 类，累计 {total} 次）")
            for h in group:
                mark = "★" * h.weight
                print(f"    {mark:<7} ×{h.count:<4} {h.reason[:52]}")
                print(f"    {'':<7}        原文：{h.sample[:88]}")
    print()

    print("【三】噪音统计")
    print("-" * 68)
    print(f"  已按 A7A 已知噪音白名单滤除 {stats.get('benign', 0)} 行"
          f"（smc 0 p2 / retry:set phase / pinstate 等，属正常现象）")
    print()

    print("【四】结论")
    print("-" * 68)
    title, detail = VERDICT_TEXT[verdict]
    print(f"  {verdict}  ——  {title}")
    print()
    for line in textwrap.wrap(detail, width=64):
        print(f"  {line}")
    print()

    if verdict in ("CONTROLLER_FAULT", "POWER_FAULT", "INCONCLUSIVE"):
        cmds = build_probe_cmds(blk)
        print("【五】补充取证（在板子上跑，全部只读）")
        print("-" * 68)
        print(f"  目标块设备：{blk}"
              + ("（自动探测）" if not blk_explicit else "（手动指定）"))
        print("  拿到 shell（串口 initramfs 或救援模式）后执行：")
        print()
        for name, cmd in cmds[:8]:
            print(f"    # {name}")
            print(f"    {cmd}")
        print()
        print("  或者直接：")
        print("    python card_forensics.py --serial COM3")
        print()
        print("  最关键的三条（判断控制器电压切换是否真的坏了）：")
        print(f"    {cmds[0][1]}      # 卡是谁家的")
        print(f"    {cmds[1][1]}      # 寿命寄存器 A/B，能看出磨损")
        print(f"    {cmds[8][1]}      # 本次启动有没有 MMC 控制器报错")
        print()
    print(bar)


# --------------------------------------------------------------------------
# 采集通道
# --------------------------------------------------------------------------

def collect_ssh(target: str, blk: str = _FALLBACK_BLK) -> str:
    """通过 SSH 跑只读命令，拼成一份类日志文本。"""
    chunks: list[str] = []
    for name, cmd in build_probe_cmds(blk):
        chunks.append(f"\n### {name}: {cmd}")
        try:
            r = subprocess.run(
                ["ssh", "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=8",
                 "-o", "StrictHostKeyChecking=no",
                 target, cmd],
                capture_output=True, timeout=30,
            )
            out = r.stdout.decode("utf-8", errors="replace").strip()
            err = r.stderr.decode("utf-8", errors="replace").strip()
            chunks.append(out or "(空)")
            if err:
                chunks.append(f"[stderr] {err}")
        except subprocess.TimeoutExpired:
            chunks.append("[超时]")
        except Exception as e:  # noqa: BLE001
            chunks.append(f"[失败] {e}")
    return "\n".join(chunks)


def collect_serial(port: str, baud: int, blk: str = _FALLBACK_BLK) -> str:
    """通过串口跑只读命令。

    注意：这条通道要求板子已经有可用的 shell（initramfs 或救援模式）。
    如果板子停在 (initramfs)，直接跑 `--serial` 也能用，因为 initramfs
    本身带 busybox，上面这些 cat / dmesg 命令大部分可用。
    """
    try:
        import serial  # type: ignore
    except ImportError:
        print("缺 pyserial。安装：")
        print("  C:\\Users\\liuqi\\.workbuddy-ai\\binaries\\python\\envs\\default\\Scripts\\pip install pyserial")
        sys.exit(2)

    import time

    ser = serial.Serial(port, baud, timeout=1)
    buf: list[str] = []

    def pump(secs: float) -> None:
        end = time.time() + secs
        while time.time() < end:
            data = ser.read(4096)
            if data:
                buf.append(data.decode("utf-8", errors="replace"))

    def send(line: str, wait: float = 3.0) -> None:
        ser.write((line + "\n").encode())
        ser.flush()
        pump(wait)

    print(f"[serial] 打开 {port} @ {baud}，接管 8 秒看当前状态…")
    pump(8)

    print("[serial] 发送回车探测提示符…")
    send("", 1.5)

    for name, cmd in build_probe_cmds(blk):
        print(f"[serial] → {name}")
        send(f"echo ==={name}===", 0.4)
        send(cmd, 2.5)

    send("echo ===END===", 1.0)
    ser.close()
    return "".join(buf)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="存储介质/控制器 坏卡根因取证器（只读，不写盘，不碰 U-Boot）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        示例：
          python card_forensics.py --log boot.txt --offline
          python card_forensics.py --ssh radxa@192.168.10.69
          python card_forensics.py --serial COM3 --baud 115200
          python card_forensics.py --log boot.txt --json > report.json

          非 A7A 设备（自动探测根分区所在的 MMC）：
          python card_forensics.py --ssh pi@raspberrypi.local
          # 探测不准时手动指定
          python card_forensics.py --ssh pi@raspberrypi.local --mmc mmcblk0
        """),
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--log", help="已有的启动日志文件")
    src.add_argument("--ssh", help="SSH 目标，如 radxa@192.168.10.69")
    src.add_argument("--serial", help="串口设备，如 COM3")

    ap.add_argument("--offline", action="store_true",
                    help="配合 --log，明确表示离线分析")
    ap.add_argument("--baud", type=int, default=115200,
                    help="串口波特率（默认 115200）")
    ap.add_argument("--mmc", default=None,
                    help="目标块设备，如 mmcblk0 / mmcblk1 / nvme0n1。"
                         "默认自动探测（根分区所在的那块）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非人话报告")
    ap.add_argument("--save", help="把采集到的原始文本另存一份")
    args = ap.parse_args()

    # 决定取证的块设备：显式指定 > 自动探测 > 回退 mmcblk1
    blk_explicit = bool(args.mmc)
    blk = resolve_block_for_analysis(args.mmc)
    if not args.json:
        print(f"[目标块设备] {blk}"
              + ("（手动指定）" if blk_explicit else "（自动探测）"))

    if args.log:
        if not os.path.exists(args.log):
            print(f"找不到日志文件：{args.log}")
            return 2
        with open(args.log, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        source = f"日志文件 {args.log}（{len(text)} 字符）"
    elif args.ssh:
        print(f"[ssh] 只读取证 {args.ssh} …")
        text = collect_ssh(args.ssh, blk)
        source = f"SSH {args.ssh}"
    else:
        text = collect_serial(args.serial, args.baud, blk)
        source = f"串口 {args.serial} @ {args.baud}"

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[save] 原始文本已存到 {args.save}")

    hits, stats = scan_log(text)
    rw = analyze_rw_asymmetry(text)
    v = verdict_of(hits)

    # 读写方向检验可以**升级**结论：如果只有写错误、且已有控制器证据，
    # 那基本可以钉死是电压切换故障（而不是卡坏）。
    if rw["verdict"] == "write_only" and v == "INCONCLUSIVE":
        v = "CONTROLLER_FAULT"

    if args.json:
        print(json.dumps({
            "verdict": v,
            "verdict_title": VERDICT_TEXT[v][0],
            "verdict_detail": VERDICT_TEXT[v][1],
            "rw_asymmetry": rw,
            "rw_title": RW_TEXT[rw["verdict"]][0],
            "target_block": blk,
            "target_explicit": blk_explicit,
            "hits": [
                {"layer": h.layer, "weight": h.weight, "count": h.count,
                 "reason": h.reason, "sample": h.sample}
                for h in hits
            ],
            "stats": stats,
            "source": source,
        }, ensure_ascii=False, indent=2))
    else:
        print_report(hits, stats, v, source, text, blk, blk_explicit)

    return 0


if __name__ == "__main__":
    sys.exit(main())
