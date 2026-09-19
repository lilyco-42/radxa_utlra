#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rsh.py —— 最小 SSH 命令执行器（**只跑命令，不往板子写任何东西**）

## 为什么要单独写这个

`board-remote.py` 是给"一键体检/修复"用的，它会：
  - 安装公钥（写 ~/.ssh/authorized_keys）
  - 推送脚本到 /tmp（写盘）

在"板子刚重刷、用户明确要求不准损坏"的场景下，**连这些都不该做**。
本工具只建立 SSH 会话执行命令，不在远端创建/修改任何文件。

## 用法

    # 密码从环境变量读（不落盘、不进 shell history）
    export BOARD_PW=xxx
    python rsh.py --host 192.168.10.165 --cmd "uptime"

    # 多条命令
    python rsh.py --host 192.168.10.165 --cmd "id; hostname; uname -r"

    # 需要 sudo 时显式声明（会用 sudo -S 从 stdin 喂密码）
    python rsh.py --host 192.168.10.165 --sudo --cmd "dumpe2fs -h /dev/mmcblk1p3"

    # 从文件读命令（每行一条，支持 # 注释）
    python rsh.py --host 192.168.10.165 --sudo --file checks.txt

    # 干跑：只打印将要执行什么，不连接
    python rsh.py --host 192.168.10.165 --cmd "reboot" --dry-run

## 安全护栏

- 默认**拒绝**明显会改系统的命令（reboot / mkfs / dd / mount -o remount / apt install 等），
  必须显式加 `--i-know-what-im-doing` 才放行。
- `--dry-run` 只打印不执行。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# paramiko 只在**真正要连接时**才需要。
# 放在模块顶层会让 `--help` / 护栏检查在没有 paramiko 的环境里直接退出 ——
# 查帮助不该依赖运行依赖，所以改成惰性导入。
paramiko = None  # type: ignore[assignment]


def _need_paramiko():
    global paramiko
    if paramiko is None:
        try:
            import paramiko as _p
        except ImportError:
            print("需要 paramiko。用带 paramiko 的 Python 运行，例如：\n"
                  "  C:/Users/<你>/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe\n"
                  "或先 pip install paramiko", file=sys.stderr)
            sys.exit(1)
        paramiko = _p
    return paramiko

# ── 危险命令护栏 ──────────────────────────────────────────────────────
# 这些是"会改变系统状态或大幅增加写盘"的动作。默认拒绝。
#
# ⚠️ 关键：必须匹配「**被当作命令执行**」，而不是「出现在字符串里」。
# 踩过的坑：最初用裸 `\bfsck\b`，结果把只读查询也拦了 ——
#   · `systemctl show systemd-fsck-root.service`   ← 服务名里含 fsck
#   · `journalctl | grep 'fsck-root'`              ← 搜索词里含 fsck
# 两者都是纯读取，却因为误报被拒。误报会让人绕过护栏，比不设更糟。
#
# 解法：要求工具名出现在**命令起始位置**（行首 / ; & | 之后 / sudo 之后）。
_CMD = r"(?:^|[;&|]\s*|\bsudo\s+|\bnice\s+|\benv\s+|\bxargs\s+)"

DANGEROUS = [
    # (匹配模式, 人类可读的命令名, 为什么危险)
    (rf"{_CMD}(?:reboot|shutdown|poweroff|halt)\b",
     "reboot / shutdown",
     "重启或关机 —— 启动时 ext4lazyinit 会批量回写块位图，在写路径可疑的卡上可能当场损坏"),
    (rf"{_CMD}mkfs(?:\.\w+)?\b", "mkfs", "格式化，数据全丢"),
    (rf"{_CMD}dd\b", "dd", "裸写块设备，写错设备即毁盘"),
    (rf"{_CMD}(?:fdisk|parted|sgdisk|sfdisk)\b", "fdisk / parted", "改分区表"),
    (rf"{_CMD}mount\b[^;|&]*\bremount\b", "mount -o remount", "重新挂载 —— 会改变写入行为"),
    (rf"{_CMD}(?:e2fsck|fsck(?:\.\w+)?|tune2fs|dumpe2fs\s+-[a-zA-Z]*[fw])\b",
     "fsck / e2fsck / tune2fs",
     "文件系统检查或调参 —— 大量写盘。（注：`dumpe2fs -h` 是只读的，不拦）"),
    (rf"{_CMD}apt(?:-get)?\s+(?:install|upgrade|dist-upgrade|remove|purge)\b",
     "apt install / upgrade / remove", "装或卸软件包"),
    (rf"{_CMD}sysctl\s+-[wp]\b", "sysctl -w / -p", "改内核参数 —— 可能改变回写策略"),
    (r"\brm\s+-rf\s+/(?:\s|$)", "rm -rf /", "递归删根目录"),
    (r">\s*/etc/(?:fstab|sysctl)", "改写 /etc/fstab 或 sysctl", "直接改系统配置"),
    (rf"{_CMD}(?:saveenv|mmc\s+write)\b",
     "saveenv / mmc write",
     "动 U-Boot —— 项目硬约束，绝对禁止"),
]


def check_dangerous(cmd: str) -> list[str]:
    hits = []
    for pat, name, why in DANGEROUS:
        if re.search(pat, cmd, re.I):
            hits.append((name, why))
    return hits


def run(host: str, user: str, password: str, cmd: str,
        use_sudo: bool, timeout: int, key_path: str | None) -> tuple[int, str, str]:
    pk = _need_paramiko()
    c = pk.SSHClient()
    c.set_missing_host_key_policy(pk.AutoAddPolicy())

    kwargs = dict(hostname=host, username=user, timeout=15,
                  allow_agent=False, look_for_keys=False)
    if key_path and os.path.exists(os.path.expanduser(key_path)):
        kwargs["key_filename"] = os.path.expanduser(key_path)
        kwargs["password"] = password          # 允许回退
    else:
        kwargs["password"] = password

    c.connect(**kwargs)

    if use_sudo:
        # -S 从 stdin 读密码；-p '' 不打印提示
        wrapped = f"sudo -S -p '' bash -lc {shell_quote(cmd)}"
        stdin, stdout, stderr = c.exec_command(wrapped, timeout=timeout)
        stdin.write(password + "\n")
        stdin.flush()
    else:
        stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)

    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    c.close()
    return rc, out, err


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="最小 SSH 执行器（不写板子任何文件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="radxa")
    ap.add_argument("--password", default=os.environ.get("BOARD_PW", ""),
                    help="默认读环境变量 BOARD_PW（避免落盘）")
    ap.add_argument("--key", default="~/.ssh/id_ed25519")
    ap.add_argument("--cmd", help="要执行的命令")
    ap.add_argument("--file", help="从文件读命令（每行一条）")
    ap.add_argument("--sudo", action="store_true", help="用 sudo 执行")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--i-know-what-im-doing", dest="force", action="store_true",
                    help="放行危险命令（默认拒绝）")
    args = ap.parse_args()

    if not args.cmd and not args.file:
        ap.print_help()
        return 0

    cmds = []
    if args.cmd:
        cmds.append(args.cmd)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    cmds.append(line)

    # 护栏
    if not args.force:
        blocked = []
        for c in cmds:
            for name, why in check_dangerous(c):
                blocked.append((c, name, why))
        if blocked:
            print("⛔ 命令被护栏拦下（如确需执行，加 --i-know-what-im-doing）：", file=sys.stderr)
            for c, name, why in blocked:
                print(f"   {c}", file=sys.stderr)
                print(f"     └─ 命中【{name}】：{why}", file=sys.stderr)
            return 3

    if args.dry_run:
        print(f"[dry-run] 目标 {args.user}@{args.host}  sudo={args.sudo}")
        for c in cmds:
            print(f"   $ {c}")
        return 0

    if not args.password:
        print("缺少密码：设 BOARD_PW 环境变量或 --password", file=sys.stderr)
        return 2

    rc_all = 0
    for c in cmds:
        rc, out, err = run(args.host, args.user, args.password, c,
                           args.sudo, args.timeout, args.key)
        print(f"$ {c}")
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print(f"[stderr] {err.rstrip()}", file=sys.stderr)
        print(f"[exit {rc}]")
        print()
        if rc != 0:
            rc_all = rc

    return rc_all


if __name__ == "__main__":
    sys.exit(main())
