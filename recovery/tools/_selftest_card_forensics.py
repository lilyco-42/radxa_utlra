#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 card_forensics.py 的判定逻辑做自检。

用 2026-09-18 用户贴出的真实日志片段构造样本，验证：
  1. 控制器类证据能被抓到并定案为 CONTROLLER_FAULT
  2. 噪音（smc 0 p2 / pinstate）不会污染判定
  3. 供电类证据单独出现时给 POWER_FAULT
  4. 只有卡层面强证据时给 CARD_FAULT
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_forensics as cf  # noqa: E402

CASES = [
    ("真实日志片段（控制器电压切换失败）", """
U-Boot 2026.04-3-boot-dlan17-g24da4dae7637-dirty
smc 0 p2 err, cmd 1, RTO !!
retry:set phase
smc 0 p2 err, cmd 1, RTO !!
retry:give up
Card did not respond to voltage select! : -110
manual set ocr
sunxi_mmc_host-4022000.sdmmc: [WARN]: Cann't get uart0 pinstate
sunxi_mmc_host-4022000.sdmmc: [WARN]: Cann't get pin bias hs pinstate
sunxi-ufs-pltfm: link startup failed
OPP not supported by regulators
failed to find dram_clk
EXT4-fs error (device mmcblk1p3): ext4_validate_block_bitmap:421: bg 64: bad block bitmap checksum
EXT4-fs (mmcblk1p3): Remounting filesystem read-only
""", "CONTROLLER_FAULT"),

    ("只有供电问题", """
OPP not supported by regulators
OPP not supported by regulators
failed to find dram_clk
pdtest ... failed with error -110
""", "POWER_FAULT"),

    ("只有卡结构性损坏", """
bad magic in superblock
EXT4-fs error (device mmcblk1p3): bad block bitmap checksum
block count exceeds size of device
""", "CARD_FAULT"),

    ("纯噪音（应判定 INCONCLUSIVE）", """
smc 0 p2 err, cmd 1, RTO !!
retry:set phase
reg-virt-consumer: failed to find dram_clk
hctosys: unable to open rtc device
NSI_PMU: unknown pin
""", "INCONCLUSIVE"),

    ("空日志", "", "INCONCLUSIVE"),
]


def main() -> int:
    ok = 0
    for name, text, want in CASES:
        hits, stats = cf.scan_log(text)
        got = cf.verdict_of(hits)
        flag = "PASS" if got == want else "FAIL"
        if got == want:
            ok += 1
        print(f"[{flag}] {name}")
        print(f"        期望 {want}  实际 {got}  "
              f"（证据 {len(hits)} 条，滤除噪音 {stats.get('benign', 0)} 行）")
        if got != want:
            for h in hits:
                print(f"          - {h.layer} w={h.weight} x{h.count}: {h.reason[:40]}")
    print()
    print(f"{ok}/{len(CASES)} 通过")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
