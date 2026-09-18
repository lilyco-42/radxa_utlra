#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board-remote.py —— 从 Windows 侧一键远程体检/修复开发板

它解决的实际问题（都是踩过的坑）：
  1. Windows 没有 sshpass，paramiko 可直接走密码登录，不用装第三方工具。
  2. 板子重刷后 SSH 主机密钥会变，ssh 会直接拒绝连接 ——
     这里自动识别并清理旧记录（先备份 known_hosts）。
  3. 板子 IP 会变 —— 先用 lan_sweep 自动找，找到再连。
  4. 免密登录 —— 首次用密码登录后顺手把公钥装上，之后直接 ssh 即可。

用法:
  # 只体检（不动板子）
  python board-remote.py
  # 找板子 + 体检
  python board-remote.py --find
  # 应用安全类修复
  python board-remote.py --apply
  # 应用修复 + 写路径加固
  python board-remote.py --apply --harden
  # 指定板子地址与密码
  python board-remote.py --host 192.168.10.165 --password radxa
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_USER = "radxa"
DEFAULT_PASSWORD = "radxa"
SSH_DIR = Path.home() / ".ssh"
KEY_PATH = SSH_DIR / "id_ed25519"

FILES_TO_PUSH = ["board-fix.sh", "device_profile.py"]


def out(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    out(f"\n▌{msg}")


def ok(msg: str) -> None:
    out(f"  ✓ {msg}")


def warn(msg: str) -> None:
    out(f"  ! {msg}")


def bad(msg: str) -> None:
    out(f"  ✗ {msg}")


def info(msg: str) -> None:
    out(f"  · {msg}")


# ══════════════════════════════════════════════════════════════════════
# 第 1 步：找板子
# ══════════════════════════════════════════════════════════════════════

def find_board(subnet: str | None = None) -> str | None:
    """用 lan_sweep 扫网，挑出「跑着 Debian/OpenSSH 的那台」。"""
    step("扫描局域网找开发板")
    sweep = HERE / "lan_sweep.py"
    if not sweep.exists():
        warn(f"没找到 {sweep}，跳过自动发现")
        return None

    cmd = [sys.executable, str(sweep), "--identify", "--json"]
    if subnet:
        cmd.insert(2, subnet)

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.SubprocessError as e:
        warn(f"扫描失败: {e}")
        return None

    text = r.stdout.decode("utf-8", errors="replace")
    # --json 输出前后可能夹着别的提示行，只取 JSON 主体
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        warn("扫描没有返回可解析结果")
        if r.stderr:
            info(r.stderr.decode("utf-8", errors="replace").strip()[:200])
        return None

    import json
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        warn("扫描结果不是合法 JSON")
        return None

    hosts = data.get("hosts", [])
    candidates = []
    for h in hosts:
        if 22 not in h.get("open_ports", []):
            continue
        banner = (h.get("banner") or "").lower()
        score = 0
        # OpenSSH on Debian 是最强的特征（开发板几乎都是这个组合）
        if "openssh" in banner:
            score += 2
        if "debian" in banner:
            score += 3
        if "OpenSSH_for_Windows" in (h.get("banner") or ""):
            score -= 10          # 别连回自己这台 Windows
        candidates.append((score, h["ip"], h.get("banner", "")))

    if not candidates:
        warn(f"扫了 {data.get('subnet')}.0/24，没找到开着 SSH 的设备")
        return None

    candidates.sort(reverse=True)
    info(f"扫到 {len(candidates)} 台 SSH 设备：")
    for score, ip, banner in candidates:
        mark = "★" if score == candidates[0][0] else " "
        out(f"    {mark} {ip:<16} {banner[:60]}")

    best_score, best_ip, _ = candidates[0]
    if best_score <= 0:
        warn("没有一台像开发板（都是 Windows 自己或陌生设备）")
        return None
    ok(f"选中 {best_ip}")
    return best_ip


# ══════════════════════════════════════════════════════════════════════
# 第 2 步：连上去（含 known_hosts 自愈）
# ══════════════════════════════════════════════════════════════════════

def have_key() -> bool:
    return KEY_PATH.exists() and (KEY_PATH.with_suffix(".pub")).exists()


def ensure_key() -> None:
    if have_key():
        return
    step("生成 SSH 密钥")
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    r = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(KEY_PATH)],
        capture_output=True,
    )
    if r.returncode == 0:
        ok(f"已生成 {KEY_PATH}")
    else:
        warn("ssh-keygen 失败，将只用密码登录")


def purge_known_host(host: str) -> None:
    """板子重刷后主机密钥必变。清掉旧记录，但先备份。"""
    kh = SSH_DIR / "known_hosts"
    if not kh.exists():
        return
    content = kh.read_text(encoding="utf-8", errors="replace")
    if host not in content:
        return
    step("主机密钥变化处理")
    backup = kh.with_name(f"known_hosts.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(content, encoding="utf-8")
    info(f"已备份到 {backup.name}")
    lines = [ln for ln in content.splitlines()
             if host not in ln and not ln.startswith(f"{host} ")]
    kh.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"已清除 {host} 的旧主机密钥（重刷系统后这是正常的）")


def ssh_run(host: str, user: str, password: str, cmd: str,
            timeout: int = 300, use_key: bool = True):
    """执行远程命令。优先密钥，失败回退密码。"""
    try:
        import paramiko
    except ImportError:
        bad("缺少 paramiko，请运行: pip install paramiko")
        sys.exit(3)

    last_err = None
    # 先试密钥
    if use_key and have_key():
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(hostname=host, username=user,
                      key_filename=str(KEY_PATH),
                      timeout=15, banner_timeout=15, auth_timeout=15,
                      look_for_keys=False, allow_agent=False)
            try:
                _, so, se = c.exec_command(cmd, timeout=timeout)
                rc = so.channel.recv_exit_status()
                return rc, so.read().decode("utf-8", "replace"), \
                       se.read().decode("utf-8", "replace")
            finally:
                c.close()
        except paramiko.AuthenticationException:
            pass          # 密钥没装上，走密码
        except Exception as e:
            last_err = e

    # 密码
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(hostname=host, username=user, password=password,
                  timeout=15, banner_timeout=15, auth_timeout=15,
                  look_for_keys=False, allow_agent=False)
        try:
            _, so, se = c.exec_command(cmd, timeout=timeout)
            rc = so.channel.recv_exit_status()
            return rc, so.read().decode("utf-8", "replace"), \
                   se.read().decode("utf-8", "replace")
        finally:
            c.close()
    except Exception as e:
        bad(f"连接失败: {type(e).__name__}: {e}")
        if last_err:
            info(f"（密钥方式也失败: {last_err}）")
        sys.exit(4)


def install_key(host: str, user: str, password: str) -> bool:
    """把本机公钥装进板子，之后免密。"""
    if not have_key():
        return False
    pub = KEY_PATH.with_suffix(".pub").read_text(encoding="utf-8").strip()
    step("安装公钥（之后免密登录）")
    cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qxF '{pub}' ~/.ssh/authorized_keys 2>/dev/null || "
        f"echo '{pub}' >> ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys; echo KEY_OK"
    )
    rc, so, se = ssh_run(host, user, password, cmd, use_key=False)
    if "KEY_OK" in so:
        ok("公钥已安装 —— 以后可以 ssh radxa@%s 直接进" % host)
        return True
    warn("公钥安装失败，后续仍需密码")
    return False


# ══════════════════════════════════════════════════════════════════════
# 第 3 步：推送脚本
# ══════════════════════════════════════════════════════════════════════

def push_scripts(host: str, user: str, password: str) -> str:
    """把 board-fix.sh 等推上去。返回远端目录。"""
    step("推送脚本到板子")
    try:
        import paramiko
    except ImportError:
        bad("缺少 paramiko")
        sys.exit(3)

    remote_dir = "/tmp/board-fix-push"

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kw = dict(hostname=host, username=user, timeout=15,
                  banner_timeout=15, auth_timeout=15,
                  look_for_keys=False, allow_agent=False)
        if have_key():
            kw["key_filename"] = str(KEY_PATH)
        else:
            kw["password"] = password
        try:
            c.connect(**kw)
        except paramiko.AuthenticationException:
            if "password" not in kw:
                kw.pop("key_filename", None)
                kw["password"] = password
                c.connect(**kw)
            else:
                raise

        c.exec_command(f"mkdir -p {remote_dir}")[1].channel.recv_exit_status()
        sftp = c.open_sftp()
        pushed = []
        for name in FILES_TO_PUSH:
            local = HERE / name
            if not local.exists():
                warn(f"本地没有 {name}，跳过")
                continue
            sftp.put(str(local), f"{remote_dir}/{name}")
            pushed.append(name)
        try:
            sftp.chmod(f"{remote_dir}/board-fix.sh", 0o755)
        except OSError:
            pass
        sftp.close()
        if pushed:
            ok(f"已推送: {', '.join(pushed)}")
        else:
            bad("没有文件被推送")
            sys.exit(5)
        return remote_dir
    finally:
        c.close()


# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 Windows 一键远程体检/修复开发板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--host", help="板子 IP（不给就自动扫网找）")
    ap.add_argument("--user", default=DEFAULT_USER, help=f"用户名（默认 {DEFAULT_USER}）")
    ap.add_argument("--password", default=DEFAULT_PASSWORD,
                    help="密码（默认板子出厂密码）")
    ap.add_argument("--subnet", help="指定网段前三段，如 192.168.10")
    ap.add_argument("--find", action="store_true", help="强制先扫网找板子")
    ap.add_argument("--apply", action="store_true", help="应用安全类修复")
    ap.add_argument("--harden", action="store_true", help="额外做写路径加固")
    ap.add_argument("--only", help="只修指定项，逗号分隔（见 board-fix.sh --list）")
    ap.add_argument("--no-key", action="store_true", help="不安装公钥")
    args = ap.parse_args()

    out("开发板远程体检/修复工具")
    out("═" * 64)

    # 1) 定位
    host = args.host
    if not host or args.find:
        found = find_board(args.subnet)
        if found:
            host = found
        elif not host:
            bad("找不到板子。请显式指定：--host 192.168.x.x")
            info("提示：板子 IP 可以用路由器后台看，或在板子上执行 ip a")
            return 2
        else:
            warn(f"自动扫描没找到，沿用你给的 {host}")

    step(f"目标板子: {host}")
    ok(f"用户: {args.user}")

    # 2) 连通性
    out()
    rc, so, se = ssh_run(host, args.user, args.password, "echo PING_OK; hostname",
                         use_key=False)
    if "PING_OK" not in so:
        # 典型症状：主机密钥变了
        if "host key" in se.lower() or "verification" in se.lower():
            purge_known_host(host)
            rc, so, se = ssh_run(host, args.user, args.password,
                                 "echo PING_OK; hostname", use_key=False)
        if "PING_OK" not in so:
            bad("连不上。可能原因：")
            info("1. 板子 IP 变了 → 用 --find 重新扫")
            info("2. 板子没启动完 → 等 30 秒再试")
            info("3. 密码不对 → 用 --password 指定")
            if se.strip():
                info(f"原始报错: {se.strip()[:200]}")
            return 3

    board_host = so.strip().splitlines()[-1] if so.strip() else "?"
    ok(f"连上了: {board_host}")

    # 3) 公钥
    if not args.no_key:
        try:
            install_key(host, args.user, args.password)
        except SystemExit:
            raise
        except Exception as e:
            warn(f"公钥安装出错（不影响后续）: {e}")

    # 4) 推脚本
    remote_dir = push_scripts(host, args.user, args.password)

    # 5) 执行
    flags = "--check"
    if args.apply:
        flags = "--apply"
        if args.harden:
            flags += " --harden"
    if args.only:
        flags += f" --only={args.only}"

    step(f"在板子上执行: board-fix.sh {flags}")
    cmd = (f"cd {remote_dir} && chmod +x board-fix.sh && "
           f"echo {args.password} | sudo -S ./board-fix.sh {flags}")
    rc, so, se = ssh_run(host, args.user, args.password, cmd, timeout=600)

    # 去掉 sudo 的密码提示噪音
    so = re.sub(r"^\[sudo\] password for \S+: ?", "", so, flags=re.M)
    out(so)
    if se.strip() and "password for" not in se:
        err = "\n".join(ln for ln in se.splitlines()
                        if "password for" not in ln)
        if err.strip():
            out("stderr:")
            out(err)

    out("═" * 64)
    if args.apply:
        out("  修复已应用。建议重启验证：")
        out(f"    ssh {args.user}@{host} sudo reboot")
    else:
        out("  只读体检完成。要真正修，加 --apply")
        out(f"    python {Path(__file__).name} --host {host} --apply")
    out()
    return rc


if __name__ == "__main__":
    sys.exit(main())
