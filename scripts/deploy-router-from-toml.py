#!/usr/bin/env python3
"""Deploy the A7A router from a local TOML file.

The TOML file is intended to stay on the board and must not be committed when it
contains credentials. This wrapper validates the config, then delegates all
network changes to the existing deploy-router.sh implementation.

Examples:
  sudo python3 scripts/deploy-router-from-toml.py --config router-config.toml
  python3 scripts/deploy-router-from-toml.py --config router-config.toml --check
  python3 scripts/deploy-router-from-toml.py --config router-config.toml --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")
MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class ConfigError(ValueError):
    pass


def get_str(section: dict, key: str, *, required: bool = True, default: str = "") -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key} 必须是字符串")
    value = value.strip()
    if required and not value:
        raise ConfigError(f"缺少 {key}")
    return value


def get_int(section: dict, key: str, *, default: int, low: int, high: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} 必须是整数")
    if not low <= value <= high:
        raise ConfigError(f"{key} 必须在 {low}..{high} 之间")
    return value


def load_config(path: Path, *, require_credentials: bool) -> dict:
    try:
        with path.open("rb") as file_obj:
            raw = tomllib.load(file_obj)
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML 格式错误: {exc}") from exc

    router = raw.get("router", {})
    pppoe = raw.get("pppoe", {})
    ap = raw.get("ap", {})
    if not all(isinstance(x, dict) for x in (router, pppoe, ap)):
        raise ConfigError("[router]、[pppoe]、[ap] 必须都是表")

    iface = get_str(router, "iface", default="end0")
    ppp_if = get_str(router, "ppp_if", default="ppp0")
    if not SAFE_NAME.fullmatch(iface) or not SAFE_NAME.fullmatch(ppp_if):
        raise ConfigError("iface/ppp_if 含有不允许的字符")

    tx_delay = get_int(router, "tx_delay", default=9, low=0, high=31)
    mtu = get_int(router, "mtu", default=1480, low=1280, high=1492)

    user = get_str(pppoe, "user", required=require_credentials)
    password = get_str(pppoe, "password", required=require_credentials)
    mac = get_str(pppoe, "mac", required=False)
    if mac and not MAC.fullmatch(mac):
        raise ConfigError("mac 必须是 AA:BB:CC:DD:EE:FF 格式")

    ssid = get_str(ap, "ssid", default="Radxa-AP")
    ap_password = get_str(ap, "password", required=require_credentials)
    if not 1 <= len(ssid) <= 32:
        raise ConfigError("ssid 长度必须是 1..32")
    if ap_password and len(ap_password) < 8:
        raise ConfigError("WiFi password 至少 8 位")
    channel = get_int(ap, "channel", default=6, low=1, high=14)

    return {
        "iface": iface,
        "ppp_if": ppp_if,
        "tx_delay": tx_delay,
        "mtu": mtu,
        "user": user,
        "password": password,
        "mac": mac,
        "ssid": ssid,
        "ap_password": ap_password,
        "channel": channel,
    }


def build_command(script: Path, config: dict, *, check: bool) -> list[str]:
    if check:
        return ["bash", str(script), "--check"]

    command = [
        "bash",
        str(script),
        "--all",
        "--iface",
        config["iface"],
        "--ppp-if",
        config["ppp_if"],
        "--tx-delay",
        str(config["tx_delay"]),
        "--mtu",
        str(config["mtu"]),
        "--user",
        config["user"],
        "--pass",
        config["password"],
        "--ssid",
        config["ssid"],
        "--ap-pass",
        config["ap_password"],
        "--channel",
        str(config["channel"]),
    ]
    if config["mac"]:
        command.extend(["--mac", config["mac"]])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description="从 TOML 一键配置 A7A PPPoE + WiFi AP + NAT")
    parser.add_argument("--config", default="router-config.toml", help="本地 TOML 配置文件")
    parser.add_argument("--check", action="store_true", help="只体检，不读取或要求账号密码")
    parser.add_argument("--dry-run", action="store_true", help="只校验并显示脱敏后的执行计划")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    script = Path(__file__).resolve().with_name("deploy-router.sh")
    try:
        config = load_config(config_path, require_credentials=not args.check)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    if not script.is_file():
        print(f"找不到路由部署脚本: {script}", file=sys.stderr)
        return 2

    command = build_command(script, config, check=args.check)
    if args.dry_run:
        secrets = {config.get("user"), config.get("password"), config.get("ap_password")}
        redacted = ["***" if item in secrets and item else item for item in command]
        print("执行计划（账号和密码均已隐藏）:")
        print(" ".join(redacted))
        print("配置文件仅用于本机；不要把含密码的 TOML 提交到 GitHub。")
        return 0

    if os.geteuid() != 0:
        print("请用 sudo 执行：sudo python3 scripts/deploy-router-from-toml.py --config router-config.toml", file=sys.stderr)
        return 2

    # 不打印 command，避免把 PPPoE/WiFi 密码写入日志。
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
