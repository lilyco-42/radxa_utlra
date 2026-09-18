#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 vp/config.yaml 的 LLM 配置：
  1. 从 ~/.bash_history 恢复 OpenRouter key 并校验
  2. 换成实测可用的免费模型
只改 llm.api_key / llm.model 两行，保留其余注释与排版。
"""
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

CFG = os.path.expanduser("~/vp/config.yaml")
HIST = os.path.expanduser("~/.bash_history")
MODEL = "inclusionai/ling-3.0-flash-vl:free"


def get_key():
    text = open(HIST, "r", errors="replace").read()
    keys = re.findall(r"sk-or-v1-[A-Za-z0-9]{16,}", text)
    if not keys:
        sys.exit("未在 history 中找到 OpenRouter key")
    uniq = list(dict.fromkeys(keys))
    print("history 中找到 %d 个不同 key" % len(uniq))
    return uniq


def test_key(key):
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:
        return False, str(e)
    d = data.get("data", {})
    return True, "usage=%s free_tier=%s" % (d.get("usage"), d.get("is_free_tier"))


def test_chat(key, model):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：可用"}],
        "max_tokens": 40,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "HTTP-Referer": "https://github.com/",
            "X-Title": "vp-pipeline",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return False, "HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:160])
    except Exception as e:
        return False, str(e)
    if "error" in data:
        return False, str(data["error"])[:160]
    msg = data["choices"][0]["message"].get("content") or ""
    return True, repr(msg.strip()[:40])


def value_of(line, field):
    """取出 '  field: <值>  # 注释' 里的 <值>，去掉引号与注释。"""
    m = re.match(r"^([ \t]*" + field + r":[ \t]*)(.*)$", line)
    if not m:
        return None
    return m.group(1), m.group(2).split("#", 1)[0].strip().strip('"').strip("'").strip()


def patch(raw, field, new_value, expect_empty=True):
    lines = raw.splitlines(keepends=True)
    hit = -1
    for i, line in enumerate(lines):
        parsed = value_of(line, field)
        if parsed is None:
            continue
        prefix, val = parsed
        if expect_empty and val:
            continue
        if not expect_empty and not val:
            continue
        eol = "\n" if line.endswith("\n") else ""
        rest = line[len(prefix):]
        comment = ""
        if "#" in rest:
            # 必须把 '#' 一起还原，否则剩下的中文注释会变成 YAML 里的非法标量
            comment = " #" + rest.split("#", 1)[1].rstrip("\n")
        lines[i] = "%s\"%s\"%s%s" % (prefix, new_value, comment, eol)
        hit = i
        break
    if hit < 0:
        return -1, raw
    return hit, "".join(lines)


def main():
    raw = open(CFG, "r", encoding="utf-8").read()

    keys = get_key()
    good = None
    for i, k in enumerate(keys, 1):
        ok, info = test_key(k)
        print("  key#%d 校验: %s  %s" % (i, "OK" if ok else "FAIL", info))
        if ok and good is None:
            good = k
    if good is None:
        sys.exit("history 中的 key 均无效")

    ok, info = test_chat(good, MODEL)
    print("  目标模型 %s: %s  %s" % (MODEL, "OK" if ok else "FAIL", info))
    if not ok:
        sys.exit("目标模型不可用，中止改写")

    new_raw = raw
    hit_key, new_raw = patch(new_raw, "api_key", good, expect_empty=True)
    if hit_key < 0:
        print("注意: 未找到空 api_key 行（可能已填），跳过")
    else:
        print("已定位 api_key 行 #%d" % (hit_key + 1))

    hit_model, new_raw = patch(new_raw, "model", MODEL, expect_empty=False)
    if hit_model < 0:
        print("注意: 未找到 model 行，跳过")
    else:
        print("已定位 model 行 #%d" % (hit_model + 1))

    if new_raw == raw:
        print("配置无变化")
        return
    shutil.copy2(CFG, CFG + ".bak")
    with open(CFG, "w", encoding="utf-8") as f:
        f.write(new_raw)
    os.chmod(CFG, 0o600)
    print("已写入 %s（备份 config.yaml.bak，权限 600）" % CFG)

    check = open(CFG, "r", encoding="utf-8").read()
    for line in check.splitlines():
        if re.match(r"^[ \t]*(api_key|model|base_url|provider):", line):
            shown = re.sub(r'(")[^"]{6,}(")', r"\1<redacted>\2", line)
            print("   ", shown.strip())


if __name__ == "__main__":
    main()
