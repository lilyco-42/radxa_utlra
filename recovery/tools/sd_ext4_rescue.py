#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sd_ext4_rescue.py —— 在 Windows 上只读抢救 SD 卡 ext4 分区里的数据。

## 背景

A7A 板子的 SD 卡反复损坏（详见 ROOT-CAUSE-REPORT.md）：
    · mmc@4022000 控制器 DTB 配置错误 → 干扰 MMC 写时序
    · 加上频繁直接断电 → ext4 journal 无法正常回写
    · 每次启动 ext4lazyinit 写入 → 固定 +2049 坏块 → 持续恶化

**已经启动板子 3 次，卡还在恶化。所以数据抢救必须在电脑上做，不能再启动板子。**

## 这个脚本做什么

只读地读 SD 卡上的 ext4 分区，把文件系统里的**文件**捞出来。

它能读：
    · ext4 superblock（确认文件系统参数）
    · inode 表 + 目录项（遍历目录树）
    · extent 树（定位文件数据块）
    · 直接块映射（小文件）

它**不做**：
    · 不写卡（全程只读）
    · 不修复（修复交给 WSL2 里的 e2fsck）
    · 不处理 journal（journal 回放需要 e2fsck）

**用途**：即使文件系统损坏，只要目录树和 inode 还在，就能把文件捞出来。

## 用法

    # 先看分区
    python sd_ext4_rescue.py --disk 1 --probe

    # 列出根目录
    python sd_ext4_rescue.py --disk 1 --part 3 --ls /

    # 列出 /home/radxa
    python sd_ext4_rescue.py --disk 1 --part 3 --ls /home/radxa

    # 递归抢救整个目录
    python sd_ext4_rescue.py --disk 1 --part 3 --get /home/radxa/vp --out D:/rescue

    # 抢救多个关键目录
    python sd_ext4_rescue.py --disk 1 --part 3 --rescue-all --out D:/rescue

**需要管理员权限**（读物理扇区要提权）。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
from ctypes import wintypes

# ---------------------------------------------------------------------------
# Windows 原始扇区读取
# ---------------------------------------------------------------------------

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = -1

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
kernel32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD,
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

SECTOR = 512


class RawDisk:
    """只读物理磁盘访问。"""

    def __init__(self, disk_num: int):
        self.path = rf"\\.\PhysicalDrive{disk_num}"
        self.h = kernel32.CreateFileW(
            self.path, GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        if self.h == INVALID_HANDLE_VALUE or self.h is None:
            err = ctypes.get_last_error()
            raise OSError(
                f"打不开 {self.path}（错误 {err}）。"
                f"{'需要管理员权限' if err == 5 else '磁盘不存在或未上线'}"
            )

    def read_at(self, offset: int, size: int) -> bytes:
        kernel32.SetFilePointerEx(self.h, offset, None, 0)
        buf = ctypes.create_string_buffer(size)
        n = wintypes.DWORD()
        ok = kernel32.ReadFile(self.h, buf, size, ctypes.byref(n), None)
        if not ok:
            raise OSError(f"读 {offset} 失败（错误 {ctypes.get_last_error()}）")
        return buf.raw[:n.value]

    def close(self):
        if self.h and self.h != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.h)


# ---------------------------------------------------------------------------
# GPT 分区表
# ---------------------------------------------------------------------------

def read_gpt(disk: RawDisk) -> list:
    """读 GPT，返回 [{index, first_lba, last_lba, name, type_guid}]"""
    hdr = disk.read_at(SECTOR, 512)
    if hdr[:8] != b"EFI PART":
        return []
    part_lba = struct.unpack_from("<Q", hdr, 72)[0]
    num = struct.unpack_from("<I", hdr, 80)[0]
    entsz = struct.unpack_from("<I", hdr, 84)[0]
    raw = disk.read_at(part_lba * SECTOR, num * entsz)

    GUID_FS = "{0fc63daf-8483-4772-8e79-3d69d8477de4}"
    GUID_EFI = "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}"

    parts = []
    for i in range(num):
        e = raw[i * entsz:(i + 1) * entsz]
        if len(e) < 128 or e[:16] == b"\x00" * 16:
            continue
        tg = struct.unpack_from("<HIHH", e, 0)
        tg2 = struct.unpack_from("<3H2B", e, 8)
        type_guid = format_guid(tg + tg2)
        first = struct.unpack_from("<Q", e, 32)[0]
        last = struct.unpack_from("<Q", e, 40)[0]
        nm = e[56:128].decode("utf-16-le", errors="replace").rstrip("\x00")
        kind = "EFI" if type_guid.upper() == GUID_EFI.upper() else (
            "Linux" if type_guid.upper() == GUID_FS.upper() else "其他")
        parts.append({
            "index": i + 1, "first_lba": first, "last_lba": last,
            "name": nm, "type_guid": type_guid, "kind": kind,
            "offset": first * SECTOR,
            "size": (last - first + 1) * SECTOR,
        })
    return parts


def format_guid(t) -> str:
    d1, d2, d3 = t[0], t[1], t[2]
    d4 = t[3:11]
    return (f"{d1:08x}-{d2:04x}-{d3:04x}-"
            f"{d4[0]:02x}{d4[1]:02x}-"
            f"{''.join(f'{b:02x}' for b in d4[2:8])}")


# ---------------------------------------------------------------------------
# ext4 最小只读实现
# ---------------------------------------------------------------------------

class Ext4:
    """极简 ext4 只读读取器 —— 只为抢救数据，不做完整性校验。"""

    def __init__(self, disk: RawDisk, offset: int, size: int):
        self.disk = disk
        self.base = offset
        self.size = size
        self._read_superblock()

    def pread(self, off: int, n: int) -> bytes:
        return self.disk.read_at(self.base + off, n)

    def _read_superblock(self):
        """superblock 在 offset 1024。"""
        sb = self.pread(1024, 1024)
        if sb[56:58] != b"\x53\xef":  # magic 0xEF53 (little endian)
            raise ValueError("不是 ext4 文件系统（magic 不匹配）")

        self.s_inodes_count = struct.unpack_from("<I", sb, 0)[0]
        self.s_blocks_count = struct.unpack_from("<I", sb, 4)[0]
        self.s_log_block_size = struct.unpack_from("<I", sb, 24)[0]
        self.s_blocks_per_group = struct.unpack_from("<I", sb, 32)[0]
        self.s_inodes_per_group = struct.unpack_from("<I", sb, 40)[0]
        self.s_magic = 0xEF53
        self.s_inode_size = struct.unpack_from("<H", sb, 88)[0] or 128
        self.s_feature_incompat = struct.unpack_from("<I", sb, 96)[0]
        self.s_desc_size = struct.unpack_from("<H", sb, 254)[0]
        if self.s_desc_size < 32:
            self.s_desc_size = 32

        self.block_size = 1024 << self.s_log_block_size
        self.blocks_per_group = self.s_blocks_per_group
        self.inodes_per_group = self.s_inodes_per_group
        self.inode_size = self.s_inode_size

        # 64bit feature (incompat bit 0x80)
        self.is64bit = bool(self.s_feature_incompat & 0x80)
        # extents (incompat bit 0x40)
        self.has_extents = bool(self.s_feature_incompat & 0x40)
        # flex_bg (incompat bit 0x200)
        self.has_flex_bg = bool(self.s_feature_incompat & 0x200)

        # 组描述符表位置
        if self.block_size == 1024:
            self.gdt_block = 2
        else:
            self.gdt_block = 1
        self.num_groups = (self.s_blocks_count + self.blocks_per_group - 1) // self.blocks_per_group
        self.groups = self._read_groups()

    def _read_groups(self):
        """读组描述符，返回 [{inode_table, block_bitmap, inode_bitmap}]"""
        gd_size = 64 if self.is64bit else 32
        entsz = self.s_desc_size if self.is64bit else 32
        raw = self.pread(self.gdt_block * self.block_size,
                         self.num_groups * entsz)
        groups = []
        for i in range(self.num_groups):
            g = raw[i * entsz:(i + 1) * entsz]
            if len(g) < 32:
                break
            block_bitmap = struct.unpack_from("<I", g, 0)[0]
            inode_bitmap = struct.unpack_from("<I", g, 4)[0]
            inode_table = struct.unpack_from("<I", g, 8)[0]
            it = inode_table
            if self.is64bit and len(g) >= 40:
                it |= struct.unpack_from("<I", g, 40)[0] << 32
            groups.append({
                "block_bitmap": block_bitmap,
                "inode_bitmap": inode_bitmap,
                "inode_table": it,
            })
        return groups

    def read_block(self, blk: int) -> bytes:
        return self.pread(blk * self.block_size, self.block_size)

    def read_inode(self, ino: int) -> dict | None:
        if ino < 1 or ino > self.s_inodes_count:
            return None
        grp = (ino - 1) // self.inodes_per_group
        idx = (ino - 1) % self.inodes_per_group
        if grp >= len(self.groups):
            return None
        t = self.groups[grp]["inode_table"]
        off = t * self.block_size + idx * self.inode_size
        raw = self.pread(off, self.inode_size)
        if len(raw) < 128:
            return None
        return parse_inode(raw)

    def read_dir(self, ino: int) -> list:
        """读目录，返回 [(name, inode, file_type)]"""
        node = self.read_inode(ino)
        if not node or not (node["mode"] & 0xF000) == 0x4000:
            return []
        data = self.read_file_data(node)
        entries = []
        pos = 0
        while pos + 8 <= len(data):
            ino_v, rec_len, name_len, ftype = struct.unpack_from("<IHBB", data, pos)
            if rec_len < 8 or pos + rec_len > len(data):
                break
            if ino_v != 0 and name_len > 0:
                nm = data[pos + 8:pos + 8 + name_len]
                try:
                    name = nm.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    name = nm.decode("latin-1", errors="replace")
                if name not in (".", ".."):
                    entries.append((name, ino_v, ftype))
            pos += rec_len
        return entries

    def read_file_data(self, node: dict) -> bytes:
        """读文件内容（支持 extent 和直接块）。"""
        if self.has_extents and node.get("extent_root"):
            return self._read_extents(node)
        return self._read_direct_blocks(node)

    def _read_direct_blocks(self, node: dict) -> bytes:
        """传统直接/间接块映射（仅支持直接块，够抢救小文件）。"""
        out = b""
        for blk in node["blocks"][:12]:
            if blk == 0:
                out += b"\x00" * self.block_size
            else:
                out += self.read_block(blk)
        return out[:node["size"]]

    def _read_extents(self, node: dict) -> bytes:
        """读 extent 树（ext4 默认方式）。"""
        root = node["extent_root"]
        out = bytearray()
        self._walk_extent(root, out, node["size"])
        return bytes(out[:node["size"]])

    def _walk_extent(self, node_bytes: bytes, out: bytearray, want: int):
        if len(node_bytes) < 12:
            return
        magic, entries, max_e, depth, _gen = struct.unpack_from("<HHHHI", node_bytes, 0)
        if magic != 0xF30A:
            return
        for i in range(entries):
            pos = 12 + i * 12
            if pos + 12 > len(node_bytes):
                break
            if depth == 0:
                ee_block, ee_len, ee_start_hi, ee_start_lo = struct.unpack_from(
                    "<IHHI", node_bytes, pos)
                if ee_len > 32768:
                    ee_len -= 32768  # uninitialized
                start = (ee_start_hi << 32) | ee_start_lo
                if start == 0 or ee_len == 0:
                    continue
                for b in range(ee_len):
                    if len(out) >= want:
                        return
                    try:
                        out += self.read_block(start + b)
                    except OSError:
                        out += b"\x00" * self.block_size
            else:
                ei_block, ei_leaf_lo, ei_leaf_hi, _u = struct.unpack_from(
                    "<IIHH", node_bytes, pos)
                leaf = (ei_leaf_hi << 32) | ei_leaf_lo
                try:
                    child = self.read_block(leaf)
                except OSError:
                    continue
                self._walk_extent(child, out, want)


def parse_inode(raw: bytes) -> dict:
    """解析 inode（支持 extents）。"""
    mode = struct.unpack_from("<H", raw, 0)[0]
    size = struct.unpack_from("<I", raw, 4)[0]
    size_hi = struct.unpack_from("<I", raw, 108)[0]
    if mode & 0xF000 == 0x8000 and size_hi:
        size |= size_hi << 32
    links = struct.unpack_from("<H", raw, 26)[0]
    flags = struct.unpack_from("<I", raw, 32)[0]

    node = {"mode": mode, "size": size, "links": links, "flags": flags,
            "blocks": [], "extent_root": None}

    if flags & 0x80000:  # EXT4_EXTENTS_FL
        node["extent_root"] = raw[40:100]
    else:
        for i in range(15):
            blk = struct.unpack_from("<I", raw, 40 + i * 4)[0]
            if blk:
                node["blocks"].append(blk)
    return node


# ---------------------------------------------------------------------------
# 抢救逻辑
# ---------------------------------------------------------------------------

CRITICAL_PATHS = [
    "/home/radxa/vp",
    "/home/radxa/sau",
    "/home/radxa/biliup",
    "/etc/kernel",
    "/etc/systemd/system",
    "/etc/NetworkManager",
    "/etc/fstab",
    "/boot/extlinux",
]


def walk(fs: Ext4, ino: int, path: str, out_dir: str, stats: dict, depth: int = 0):
    """递归遍历 + 保存文件。"""
    if depth > 20:
        return
    try:
        entries = fs.read_dir(ino)
    except OSError as e:
        print(f"    ! 读目录失败 {path}: {e}")
        stats["errors"] += 1
        return

    for name, child_ino, ftype in entries:
        full = f"{path.rstrip('/')}/{name}"
        try:
            node = fs.read_inode(child_ino)
        except OSError:
            stats["errors"] += 1
            continue
        if not node:
            continue

        kind = node["mode"] & 0xF000
        if kind == 0x4000:  # dir
            stats["dirs"] += 1
            walk(fs, child_ino, full, out_dir, stats, depth + 1)
        elif kind == 0x8000:  # regular file
            if node["size"] > 200 * 1024 * 1024:
                print(f"    - 跳过超大文件 {full} ({node['size']/1e6:.0f}MB)")
                stats["skipped"] += 1
                continue
            try:
                data = fs.read_file_data(node)
            except OSError as e:
                print(f"    ! 读文件失败 {full}: {e}")
                stats["errors"] += 1
                continue
            rel = full.lstrip("/")
            dest = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                with open(dest, "wb") as f:
                    f.write(data)
                stats["files"] += 1
                stats["bytes"] += len(data)
                print(f"    + {full}  ({len(data)} 字节)")
            except OSError as e:
                print(f"    ! 写失败 {dest}: {e}")
                stats["errors"] += 1
        elif kind == 0xA000:  # symlink
            stats["links"] += 1


def list_dir(fs: Ext4, path: str):
    """列目录。"""
    parts = [p for p in path.split("/") if p]
    ino = 2  # root
    for p in parts:
        found = None
        for name, child_ino, _ft in fs.read_dir(ino):
            if name == p:
                found = child_ino
                break
        if found is None:
            print(f"  ✗ 找不到 {path}")
            return
        ino = found
    entries = fs.read_dir(ino)
    print(f"  {path}  ({len(entries)} 项)")
    for name, child_ino, ftype in sorted(entries):
        node = fs.read_inode(child_ino)
        if not node:
            continue
        kind = node["mode"] & 0xF000
        mark = "d" if kind == 0x4000 else ("l" if kind == 0xA000 else "-")
        size = node["size"] if kind == 0x8000 else 0
        print(f"    {mark} {name:<40} ino={child_ino:<8} {size}")


def main() -> int:
    ap = argparse.ArgumentParser(description="只读抢救 SD 卡 ext4 数据")
    ap.add_argument("--disk", type=int, required=True, help="物理磁盘号")
    ap.add_argument("--part", type=int, help="分区号（--probe 可查）")
    ap.add_argument("--probe", action="store_true", help="只显示分区表")
    ap.add_argument("--ls", help="列目录")
    ap.add_argument("--get", help="抢救一个目录")
    ap.add_argument("--rescue-all", action="store_true", help="抢救所有关键目录")
    ap.add_argument("--out", default="D:/a7a-rescue", help="输出目录")
    args = ap.parse_args()

    bar = "=" * 68
    print()
    print(bar)
    print(f"  SD 卡只读抢救  PhysicalDrive{args.disk}")
    print(bar)

    try:
        disk = RawDisk(args.disk)
    except OSError as e:
        print(f"\n  ✗ {e}")
        print("\n  提示：")
        print("    1) 确认卡已插好（读卡器 + microSD）")
        print("    2) 用管理员权限运行")
        print("    3) 磁盘可能处于 Offline：")
        print(f"       Set-Disk -Number {args.disk} -IsOffline $false -IsReadOnly $true")
        return 2

    try:
        parts = read_gpt(disk)
        print(f"\n【分区表】(GPT)")
        if not parts:
            print("  ✗ 读不到 GPT 分区表")
            return 2
        for p in parts:
            print(f"  分区 {p['index']}  {p['kind']:<8} "
                  f"{p['size']/1e9:>8.2f} GB  LBA {p['first_lba']}-{p['last_lba']}  '{p['name']}'")

        if args.probe:
            return 0

        if not args.part:
            print("\n  用 --part N 指定分区（rootfs 一般是 3）")
            return 0

        p = next((x for x in parts if x["index"] == args.part), None)
        if not p:
            print(f"\n  ✗ 没有分区 {args.part}")
            return 2

        print(f"\n【挂载 ext4】分区 {args.part}  偏移 {p['offset']}")
        try:
            fs = Ext4(disk, p["offset"], p["size"])
        except ValueError as e:
            print(f"  ✗ {e}")
            print("  （这个分区可能不是 ext4，比如是 EFI/FAT32）")
            return 2

        print(f"  ✓ block size   = {fs.block_size}")
        print(f"  ✓ inode size   = {fs.inode_size}")
        print(f"  ✓ blocks       = {fs.s_blocks_count}")
        print(f"  ✓ inodes       = {fs.s_inodes_count}")
        print(f"  ✓ 组数          = {fs.num_groups}")
        print(f"  ✓ 64bit        = {fs.is64bit}")
        print(f"  ✓ extents      = {fs.has_extents}")

        if args.ls:
            print(f"\n【列目录】{args.ls}")
            list_dir(fs, args.ls)
            return 0

        if args.get:
            print(f"\n【抢救】{args.get}  →  {args.out}")
            os.makedirs(args.out, exist_ok=True)
            # 定位路径
            parts_p = [x for x in args.get.split("/") if x]
            ino = 2
            ok = True
            for seg in parts_p:
                found = None
                for name, ci, _ft in fs.read_dir(ino):
                    if name == seg:
                        found = ci
                        break
                if found is None:
                    print(f"  ✗ 找不到 {args.get}")
                    ok = False
                    break
                ino = found
            if ok:
                stats = {"files": 0, "dirs": 0, "links": 0, "errors": 0,
                         "bytes": 0, "skipped": 0}
                walk(fs, ino, args.get, args.out, stats)
                print(f"\n  ✓ 完成：{stats['files']} 文件 / {stats['bytes']/1e6:.1f} MB"
                      f"  （{stats['errors']} 错误）")
            return 0

        if args.rescue_all:
            print(f"\n【全量抢救关键目录】→ {args.out}")
            os.makedirs(args.out, exist_ok=True)
            stats = {"files": 0, "dirs": 0, "links": 0, "errors": 0,
                     "bytes": 0, "skipped": 0}
            for path in CRITICAL_PATHS:
                segs = [x for x in path.split("/") if x]
                ino = 2
                found_all = True
                for seg in segs:
                    found = None
                    for name, ci, _ft in fs.read_dir(ino):
                        if name == seg:
                            found = ci
                            break
                    if found is None:
                        found_all = False
                        break
                    ino = found
                if not found_all:
                    print(f"\n  ── 跳过（不存在）{path}")
                    continue
                print(f"\n  ── {path}")
                walk(fs, ino, path, args.out, stats)
            print(f"\n  ✓ 全部完成：{stats['files']} 文件 / {stats['bytes']/1e6:.1f} MB"
                  f"  （{stats['errors']} 错误）")
            print(f"  输出目录：{args.out}")
            return 0

        print("\n  用 --ls / --get / --rescue-all 之一")
        return 0

    finally:
        disk.close()


if __name__ == "__main__":
    sys.exit(main())
