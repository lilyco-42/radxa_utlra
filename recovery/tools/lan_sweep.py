#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全网段 ping 扫描 —— 找板子在哪。

Windows 的 ping 输出是 GBK，必须显式解码，否则中文"时间=xxms"会乱码。
"""
import concurrent.futures
import re
import subprocess
import sys


def ping(ip: str, timeout_ms: int = 400):
    r = subprocess.run(["ping", "-n", "1", "-w", str(timeout_ms), ip],
                       capture_output=True)
    out = r.stdout.decode("gbk", errors="replace")
    if "TTL=" in out.upper():
        m = re.search(r"[时間间][=\s]*[<]?\s*(\d+)\s*ms", out)
        return (ip, m.group(1) if m else "?")
    return None


def main() -> int:
    subnet = sys.argv[1] if len(sys.argv) > 1 else "192.168.10"
    print(f"扫描 {subnet}.0/24 (ping sweep, 64 并发)…")
    targets = [f"{subnet}.{i}" for i in range(1, 255)]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for res in ex.map(ping, targets):
            if res:
                found.append(res)
                print(f"  活: {res[0]}  {res[1]}ms")
    print()
    print(f"共发现 {len(found)} 台设备")
    return 0


if __name__ == "__main__":
    sys.exit(main())
