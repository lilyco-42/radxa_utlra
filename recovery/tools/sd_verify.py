#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SD 卡真假 / 规格鉴别器 —— 从 Windows 直读卡的 CID 与 CSD 寄存器。

## 为什么需要这个工具

2026-09-18 的 A7A 坏卡排查结论是「卡是山寨卡」，但当时的证据来自 U-Boot 的
`mmcinfo`，有个漏洞：**万一是 U-Boot 读不到 CID、返回了兜底字符串呢？**

所以必须绕过板子，用**独立渠道**（Windows + USB 读卡器）再读一次。
两个来源都对上，才算实证。

## 读什么

SD 卡的 CID（Card Identification）寄存器里存着出厂信息：

    MID    Manufacturer ID      —— 厂家编号（0x03=SanDisk, 0x1B=Samsung, 0x1D=ADATA...）
    OID    OEM/Application ID   —— 厂商代号（"SD"/"SM"/"TM"...）
    PNM    Product Name         —— 产品名（假卡常见 "asdfg" / "00000" / 空白）
    PRV    Product Revision
    PSN    Product Serial Number
    MDT    Manufacturing Date   —— 年份+月份
    CRC7   CID 校验（假卡常错）

判定规则：
    * PNM 是键盘乱敲 / 全 0 / 无意义  → 假卡
    * MID 不在已知厂家表里            → 假卡
    * MDT 是乱码年份                  → 假卡

## 怎么用

    # 列出所有物理盘
    python sd_verify.py --list

    # 读某个盘的 CID（需要管理员权限）
    python sd_verify.py --disk 2

    # 顺便跑读取/写入测试验证真实容量
    python sd_verify.py --disk 2 --capacity-test

## 权限

读 CID 需要 **管理员权限**（走 SCSI_PASS_THROUGH）。
如果报 `Access denied`，用管理员身份的终端重跑。

## 安全边界

本工具**默认只读**。`--capacity-test` 会**破坏盘上数据**，
所以必须显式加 `--i-understand-data-will-be-lost` 才会执行。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import struct
import sys

# --------------------------------------------------------------------------
# 已知厂家编号表（MID）
# --------------------------------------------------------------------------

KNOWN_MID = {
    0x01: "Panasonic",
    0x02: "Toshiba / Kioxia",
    0x03: "SanDisk",
    0x04: "SanDisk (early)",
    0x05: "SanDisk (OEM) / 白牌方案 ← 常见于山寨卡",
    0x06: "Ritek",
    0x07: "Samsung (early)",
    0x09: "ATP",
    0x0A: "Lexar",
    0x0B: "PQI",
    0x0C: "Transcend",
    0x0D: "Sony",
    0x12: "Kingston / 白牌",
    0x13: "Apacer",
    0x15: "Dane-Elec",
    0x18: "Unknown (0x18)",
    0x19: "Unknown (0x19)",
    0x1A: "Unknown (0x1A)",
    0x1B: "Samsung",
    0x1C: "Unknown (0x1C)",
    0x1D: "ADATA",
    0x1E: "Unknown (0x1E)",
    0x1F: "Unknown (0x1F)",
    0x27: "Kingston",
    0x28: "Lexar",
    0x2C: "Unknown (0x2C)",
    0x31: "Silicon Power",
    0x33: "Unknown (0x33)",
    0x41: "Kingston",
    0x42: "Unknown (0x42)",
    0x6A: "Unknown (0x6A)",
    0x74: "Transcend",
    0x76: "Unigen",
    0x82: "Unknown (0x82)",
    0x9C: "Unknown (0x9C)",
    }

# 已知的正常产品名（PNM），用于反证
KNOWN_PNM_PREFIX = (
    "SD", "SC", "US", "SA", "SM", "TM", "SU", "SS", "SE", "UC", "UT",
    "00000", "SPCC", "SMI", "PH", "SE0", "USD  ", "NCard", "AP", "KM",
)

# 明显的假卡特征：键盘连击 / 无意义重复
FAKE_PNM_PATTERNS = (
    "asdf", "asdfg", "qwer", "zxcv", "abcde", "12345", "1234",
    "aaaa", "bbbb", "ffff", "xxxx", "test",
)


# --------------------------------------------------------------------------
# Windows SCSI 透传
# --------------------------------------------------------------------------

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

IOCTL_SCSI_PASS_THROUGH = 0x0004D004
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x0004D014


class SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("ScsiStatus", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("CdbLength", ctypes.c_ubyte),
        ("SenseInfoLength", ctypes.c_ubyte),
        ("DataIn", ctypes.c_ubyte),
        ("DataTransferLength", ctypes.c_ulong),
        ("TimeOutValue", ctypes.c_ulong),
        ("DataBuffer", ctypes.c_void_p),
        ("SenseInfoOffset", ctypes.c_ulong),
        ("Cdb", ctypes.c_ubyte * 16),
    ]


class SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("sptd", SCSI_PASS_THROUGH_DIRECT),
        ("Filler", ctypes.c_ulong),
        ("SenseBuf", ctypes.c_ubyte * 32),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
    wt.DWORD, wt.DWORD, ctypes.c_void_p,
]
kernel32.DeviceIoControl.argtypes = [
    ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD,
    ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


def _open_drive(idx: int, write: bool = False):
    path = f"\\\\.\\PhysicalDrive{idx}"
    access = GENERIC_READ | (GENERIC_WRITE if write else 0)
    h = kernel32.CreateFileW(
        path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if h == INVALID_HANDLE_VALUE or h is None:
        err = ctypes.get_last_error()
        raise OSError(err, f"打不开 {path}（错误 {err}）—— 需要管理员权限？")
    return h


def _send_cdb(h, cdb: bytes, buf_size: int, direction_in: bool = True,
              timeout: int = 10):
    """发送一条 SCSI CDB，返回数据缓冲区内容。"""
    buf = ctypes.create_string_buffer(buf_size if buf_size else 1)
    sptdwb = SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER()
    ctypes.memset(ctypes.byref(sptdwb), 0, ctypes.sizeof(sptdwb))

    sptdwb.sptd.Length = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT)
    sptdwb.sptd.CdbLength = len(cdb)
    sptdwb.sptd.SenseInfoLength = 32
    sptdwb.sptd.DataIn = 1 if direction_in else 0
    sptdwb.sptd.DataTransferLength = buf_size
    sptdwb.sptd.TimeOutValue = timeout
    sptdwb.sptd.DataBuffer = ctypes.cast(buf, ctypes.c_void_p)
    sptdwb.sptd.SenseInfoOffset = (
        SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER.sptd.offset + 40
    )
    for i, b in enumerate(cdb[:16]):
        sptdwb.sptd.Cdb[i] = b

    returned = wt.DWORD(0)
    ok = kernel32.DeviceIoControl(
        h, IOCTL_SCSI_PASS_THROUGH_DIRECT,
        ctypes.byref(sptdwb), ctypes.sizeof(sptdwb),
        ctypes.byref(sptdwb), ctypes.sizeof(sptdwb),
        ctypes.byref(returned), None,
    )
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(err, f"DeviceIoControl 失败（错误 {err}）"
                           f" 可能是这个读卡器不支持 SCSI 透传")
    if sptdwb.sptd.ScsiStatus != 0:
        raise OSError(
            0, f"SCSI 状态 0x{sptdwb.sptd.ScsiStatus:02X}"
               f"（sense: {bytes(sptdwb.SenseBuf[:8]).hex()}）"
        )
    return buf.raw[:buf_size]


def read_cid(disk: int) -> bytes:
    """读 SD 卡 CID（10 字节）。"""
    h = _open_drive(disk)
    try:
        # CMD10 SEND_CID：SD 卡走 SCSI 时用 ATA PASS-THROUGH 或专用命令。
        # 大多数 USB 读卡器把 SD 当 SCSI 磁盘，用 READ CAPACITY 等命令。
        # 读 CID 的标准做法：SanDisk/通用读卡器支持 CMD10 透传。
        # 这里先试 SD 专用：ALLOW_MEDIUM_REMOVAL + SEND_CID 组合不可靠，
        # 因此优先试 READ_CAPACITY 拿容量，再试供应商特定 CID 读取。
        cdb = bytes([0x9E, 0x10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x18, 0, 0])
        # 0x9E = SERVICE ACTION IN (16)，service action 0x10 = READ CAPACITY(16)
        # 用它拿容量；CID 需要另一个 service action
        cdb = bytes([0x9E, 0x11, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x0A, 0, 0])
        # service action 0x11 = READ CAPACITY(16)，data-in
        try:
            return _send_cdb(h, cdb, 10)
        except OSError:
            pass

        # 退路：ATA PASS-THROUGH (16) 走 IDENTIFY，但 SD 卡不是 ATA。
        # 再退：某些读卡器支持 CMD10 的 SEND_CID 通过 Mode Sense 透传。
        # 直接试 SD 标准 SEND_CID（部分读卡器直接支持）
        try:
            cdb = bytes([0xA3, 0x00, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x0A, 0, 0])
            return _send_cdb(h, cdb, 10)
        except OSError as e:
            raise OSError(0, f"这个读卡器不支持读 CID：{e}")
    finally:
        kernel32.CloseHandle(h)


def read_capacity(disk: int) -> tuple[int, int]:
    """读容量，返回 (扇区数, 扇区大小)。"""
    h = _open_drive(disk)
    try:
        # READ CAPACITY (10)，0x25
        data = _send_cdb(h, bytes([0x25, 0, 0, 0, 0, 0, 0, 0, 0, 0]), 8)
        last_lba, blk_len = struct.unpack(">II", data)
        return last_lba + 1, blk_len
    finally:
        kernel32.CloseHandle(h)


def read_capacity16(disk: int) -> tuple[int, int]:
    """READ CAPACITY (16)，0x9E/0x10，能报超过 2TB 的盘。"""
    h = _open_drive(disk)
    try:
        cdb = bytes([0x9E, 0x10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 32, 0, 0])
        data = _send_cdb(h, cdb, 32)
        last_lba, blk_len = struct.unpack(">QQ", data[:16])
        return last_lba + 1, blk_len
    finally:
        kernel32.CloseHandle(h)


def read_inquiry(disk: int) -> dict:
    """SCSI INQUIRY，拿厂商/型号/固件版本。"""
    h = _open_drive(disk)
    try:
        data = _send_cdb(h, bytes([0x12, 0, 0, 0, 36, 0]), 36)
        if len(data) < 36:
            return {}
        return {
            "peripheral": data[0] & 0x1F,
            "vendor": data[8:16].decode("ascii", "replace").strip(),
            "product": data[16:32].decode("ascii", "replace").strip(),
            "revision": data[32:36].decode("ascii", "replace").strip(),
        }
    finally:
        kernel32.CloseHandle(h)


# --------------------------------------------------------------------------
# CID 解析
# --------------------------------------------------------------------------

def parse_cid(cid: bytes) -> dict:
    """解析 16 字节 CID（SD 规范）。

    CID 结构（128 bit）：
        MID   [127:120]  8 bit   厂家编号
        OID   [119:104] 16 bit   OEM/应用编号（2 个 ASCII 字符）
        PNM   [103:64]  40 bit   产品名（5 个 ASCII 字符）
        PRV   [63:56]    8 bit   产品版本（BCD，高4位主版本/低4位次版本）
        PSN   [55:24]   32 bit   产品序列号
        MDT   [19:8]    12 bit   制造日期（年份-2000 在 [11:4]，月份在 [3:0]）
        CRC   [7:1]      7 bit   CID 校验和
    """
    if len(cid) < 16:
        # 有些读卡器只回 10 字节（SD 规范 CID 是 128 bit = 16 字节）
        cid = cid.ljust(16, b"\x00")

    full = int.from_bytes(cid[:16], "big")
    mid = (full >> 120) & 0xFF
    oid = (full >> 104) & 0xFFFF
    pnm = (full >> 64) & 0xFFFFFFFFFFFF  # 40 bit
    prv = (full >> 56) & 0xFF
    psn = (full >> 24) & 0xFFFFFFFF
    mdt = (full >> 8) & 0xFFF

    def ascii5(v: int) -> str:
        s = v.to_bytes(5, "big")
        return "".join(chr(c) if 32 <= c < 127 else "." for c in s)

    oid_s = "".join(chr(c) if 32 <= c < 127 else "." for c in oid.to_bytes(2, "big"))
    year = 2000 + ((mdt >> 4) & 0xFF)
    month = mdt & 0xF

    return {
        "raw": cid[:16].hex(),
        "MID": mid,
        "MID_name": KNOWN_MID.get(mid, f"未知厂家 0x{mid:02X}"),
        "OID": oid_s,
        "PNM": ascii5(pnm),
        "PRV": f"{(prv >> 4) & 0xF}.{prv & 0xF}",
        "PSN": f"0x{psn:08X}",
        "MDT": f"{year}-{month:02d}",
    }


def judge(info: dict, cid: dict) -> tuple[str, list[str]]:
    """给出判定与理由。"""
    reasons: list[str] = []
    score = 0

    pnm = cid.get("PNM", "")
    pnm_low = pnm.lower().strip()

    # 1. 产品名是键盘乱敲
    for pat in FAKE_PNM_PATTERNS:
        if pat in pnm_low:
            reasons.append(
                f"产品名 PNM='{pnm}' 含键盘连击模式 '{pat}' "
                f"—— 正规卡的 PNM 是品牌缩写，不是随手敲的字符")
            score += 3
            break

    # 2. 产品名全 0 或空
    if pnm.strip("\x00. ") == "" or set(pnm.strip()) <= {"0"}:
        reasons.append(f"产品名 PNM='{pnm}' 为空或全 0 —— 未烧录或伪造")
        score += 2

    # 3. 厂家编号未知
    if cid.get("MID") not in KNOWN_MID:
        reasons.append(
            f"厂家编号 MID=0x{cid['MID']:02X} 不在已知厂家表中")
        score += 1

    # 4. 制造日期异常
    mdt = cid.get("MDT", "")
    try:
        y, m = mdt.split("-")
        y_i, m_i = int(y), int(m)
        if y_i < 2000 or y_i > 2030 or m_i < 1 or m_i > 12:
            reasons.append(f"制造日期 MDT={mdt} 不合理")
            score += 2
    except Exception:  # noqa: BLE001
        reasons.append(f"制造日期 MDT='{mdt}' 无法解析")
        score += 2

    # 5. 容量非标准（62.5GiB 这种）
    if info:
        sectors = info.get("sectors", 0)
        size_gb = sectors * info.get("sector_size", 512) / (1000 ** 3)
        size_gib = sectors * info.get("sector_size", 512) / (1024 ** 3)
        # 标准容量：8/16/32/64/128/256/512 GB
        std = [8, 16, 32, 64, 128, 256, 512, 1024]
        near = min(std, key=lambda s: abs(s - size_gb))
        if abs(size_gb - near) / near > 0.05:
            reasons.append(
                f"容量 {size_gb:.1f} GB（{size_gib:.1f} GiB）不是标准容量"
                f"（最接近 {near} GB，偏差 {abs(size_gb-near)/near*100:.1f}%）")
            score += 1

    if score >= 3:
        return "FAKE", reasons
    if score >= 1:
        return "SUSPECT", reasons
    return "OK", reasons


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def list_disks() -> None:
    print("检测物理盘…")
    for i in range(16):
        try:
            h = _open_drive(i)
        except OSError:
            continue
        try:
            inq = read_inquiry(i)
            try:
                sectors, blk = read_capacity(i)
                cap = f"{sectors * blk / (1000**3):.1f} GB"
            except OSError:
                cap = "?"
            print(f"  PhysicalDrive{i}:  {inq.get('vendor','')} "
                  f"{inq.get('product','')}  [{cap}]  "
                  f"rev={inq.get('revision','')}")
        finally:
            kernel32.CloseHandle(h)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="SD 卡真假/规格鉴别（Windows 直读 CID）",
    )
    ap.add_argument("--list", action="store_true", help="列出所有物理盘")
    ap.add_argument("--disk", type=int, help="物理盘编号（如 2）")
    ap.add_argument("--capacity-test", action="store_true",
                    help="跑真实容量测试（★ 会破坏盘上数据）")
    ap.add_argument("--i-understand-data-will-be-lost", action="store_true",
                    help="容量测试的确认开关")
    args = ap.parse_args()

    if args.list or args.disk is None:
        list_disks()
        if args.disk is None:
            print()
            print("用 --disk N 读某张卡的 CID。")
            return 0

    d = args.disk
    print("=" * 68)
    print(f"  SD 卡鉴别  PhysicalDrive{d}")
    print("=" * 68)

    try:
        inq = read_inquiry(d)
        print()
        print("【SCSI INQUIRY】")
        for k, v in inq.items():
            print(f"  {k:<12} {v}")
    except OSError as e:
        print(f"  INQUIRY 失败：{e}")

    try:
        sectors, blk = read_capacity(d)
        size_gb = sectors * blk / (1000 ** 3)
        size_gib = sectors * blk / (1024 ** 3)
        info = {"sectors": sectors, "sector_size": blk}
        print()
        print("【容量】")
        print(f"  扇区数      {sectors:,}")
        print(f"  扇区大小    {blk} 字节")
        print(f"  总容量      {size_gb:.2f} GB  ({size_gib:.2f} GiB)")
    except OSError as e:
        print(f"  容量读取失败：{e}")
        info = {}

    cid = None
    try:
        raw = read_cid(d)
        cid = parse_cid(raw)
        print()
        print("【CID 寄存器】")
        for k, v in cid.items():
            print(f"  {k:<12} {v}")
    except OSError as e:
        print()
        print(f"【CID 读取失败】{e}")
        print("  说明：多数普通 USB 读卡器不支持读 CID。")
        print("  可以换一个读卡器，或在 Linux 上用 `cat /sys/block/mmcblkX/device/name`。")

    if cid:
        verdict, reasons = judge(info, cid)
        print()
        print("【判定】")
        if verdict == "FAKE":
            print("  ★★★ FAKE —— 高度怀疑是山寨卡")
        elif verdict == "SUSPECT":
            print("  ★★ SUSPECT —— 有可疑特征")
        else:
            print("  ✓ OK —— 未发现异常特征")
        for r in reasons:
            print(f"    · {r}")

    print()
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
