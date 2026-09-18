#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 gen_script.py 自己的真实代码路径筛选可用模型。

关键：必须用**真实 PROMPT** + **stream=True**，否则筛出来的模型到生产就废。
判定标准：能返回 ===SCRIPT=== / ===META=== 标记、正文长度合理、标题标签能解析出来。
"""
import os
import re
import sys
import time

VP = "/home/radxa/vp"
sys.path.insert(0, VP)
os.chdir(VP)

import yaml  # noqa: E402

import gen_script  # noqa: E402

CANDIDATES = [
    "z-ai/glm-5.2:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "inclusionai/ling-3.0-flash-vl:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nex-agi/nex-n2.5-pro:free",
    "nex-agi/nex-n2.5-mini:free",
    "dots-studio/dots-3-note-preview:free",
    "thinkingmachines/inkling-small:free",
    "poolside/laguna-s-2.1:free",
    "openrouter/free",
]

TOPIC = "单板机自托管能省多少钱"
CHARS = 420


def main():
    cfg = yaml.safe_load(open(os.path.join(VP, "config.yaml"), encoding="utf-8"))
    base = dict(cfg["llm"])
    prompt = gen_script.PROMPT.format(topic=TOPIC, chars=CHARS)

    results = []
    for model in CANDIDATES:
        llm = dict(base)
        llm["model"] = model
        trial = dict(cfg)
        trial["llm"] = llm
        t0 = time.time()
        try:
            raw = gen_script.call_llm(trial, prompt)
        except SystemExit as exc:
            print("%-50s FAIL  %s" % (model, str(exc)[:90]))
            continue
        except Exception as exc:  # noqa: BLE001
            print("%-50s FAIL  %r" % (model, exc))
            continue
        dt = time.time() - t0
        script, meta = gen_script.parse_output(raw)
        markers = ("===SCRIPT===" in raw) and ("===META===" in raw)
        # 质量判据：有标记、正文 150~1200 字、标题非空、无英文思考残留
        cjk = len(re.findall(r"[\u4e00-\u9fff]", script))
        ok = markers and 150 <= len(script) <= 1200 and bool(meta["title"]) and cjk >= 100
        flag = "OK  " if ok else "BAD "
        print("%-50s %s markers=%s len=%-4d cjk=%-4d title=%-14r tags=%d %.0fs"
              % (model, flag, markers, len(script), cjk, meta["title"][:12], len(meta["tags"]), dt))
        print("      head: %r" % script[:70])
        if ok:
            results.append((model, dt, len(script)))

    print("\n==== 通过筛选的模型 ====")
    for model, dt, ln in results:
        print("   %-50s %.0fs len=%d" % (model, dt, ln))
    if results:
        print("\n推荐: %s" % results[0][0])


if __name__ == "__main__":
    main()
