#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""① 文案生成 —— 用 LLM API 写口播脚本 + 标题/简介/标签。

用法:
    python gen_script.py --topic "AI 短剧的技术原理" --out videos/xxx
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))

PROMPT = """你是一位短视频口播文案作者。请为主题「{topic}」写一条中文口播稿。

硬性要求：
1. 口语化，像真人说话，不要书面语和排比堆砌
2. 每句话短，一口气能读完；避免长定语从句
3. 开头 3 秒必须抓人（抛问题 / 给反常识结论 / 给数字）
4. 正文讲清楚 2-3 个要点，有具体例子或数字
5. 结尾一句话收束，可以引导评论
6. 全文 {chars} 字左右，不要写「大家好我是」这类废话开场
7. **只输出正文口播稿**，不要标题、不要分镜、不要任何标注

另外单独输出一份元信息。

严格按下面格式返回（不要用 markdown 代码块）：

===SCRIPT===
（口播正文，纯文本，不要空行分段）
===META===
标题: （不超过 30 字，有点击欲但不夸张）
简介: （2-3 句，说清内容，可带 3-5 个话题标签）
标签: （5 个以内，英文逗号分隔）
"""


def call_llm(cfg: dict, prompt: str) -> str:
    """调 LLM 生成文案。

    ⚠️ 必须用**流式**：NVIDIA NIM 的非流式响应极慢（实测 220s+ 直接超时），
    改成 stream=true 后同样内容只要 ~10 秒。
    """
    llm = cfg["llm"]
    if not llm.get("api_key"):
        sys.exit("错误: config.yaml 里 llm.api_key 还没填")

    payload = {
        "model": llm["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": llm.get("temperature", 0.8),
        "max_tokens": llm.get("max_tokens", 2000),
        "stream": True,
    }
    req = urllib.request.Request(
        llm["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + llm["api_key"],
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://github.com/",
            "X-Title": "vp-pipeline",
        },
        method="POST",
    )

    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    j = json.loads(data)
                    delta = j["choices"][0].get("delta", {}).get("content", "")
                    if delta:
                        chunks.append(delta)
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"LLM 请求失败 HTTP {e.code}: {body}")
    except Exception as e:
        sys.exit(f"LLM 请求失败: {e}")

    out = "".join(chunks).strip()
    if not out:
        sys.exit("LLM 返回为空（流式没有任何 chunk）")
    return out

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        sys.exit(f"LLM 返回格式异常: {json.dumps(data)[:400]}")


def parse_output(raw: str) -> tuple[str, dict]:
    """把 ===SCRIPT=== / ===META=== 拆开。"""
    script, meta = "", {"title": "", "desc": "", "tags": []}
    if "===SCRIPT===" in raw and "===META===" in raw:
        script = raw.split("===SCRIPT===", 1)[1].split("===META===", 1)[0].strip()
        meta_raw = raw.split("===META===", 1)[1]
        for line in meta_raw.splitlines():
            line = line.strip().lstrip("-* ").strip()
            if line.startswith("标题"):
                meta["title"] = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            elif line.startswith("简介"):
                meta["desc"] = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            elif line.startswith("标签"):
                t = line.split(":", 1)[-1].split("：", 1)[-1].strip()
                meta["tags"] = [x.strip() for x in t.replace("，", ",").split(",") if x.strip()]
    else:
        # 模型没按格式来，就把全文当脚本
        script = raw.strip()

    # 口播稿整理：合并多余空行，每句一行（TTS 更自然）
    script = "\n".join(l.strip() for l in script.splitlines() if l.strip())
    return script, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--out", required=True, help="输出目录 videos/{name}")
    ap.add_argument("--chars", type=int, default=420, help="目标字数")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    os.makedirs(args.out, exist_ok=True)
    raw = call_llm(cfg, PROMPT.format(topic=args.topic, chars=args.chars))
    script, meta = parse_output(raw)

    if not meta["title"]:
        meta["title"] = args.topic[:30]
    if not meta["desc"]:
        meta["desc"] = args.topic

    with open(os.path.join(args.out, "script.txt"), "w", encoding="utf-8") as f:
        f.write(script + "\n")
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"✓ 文案已生成: {args.out}/script.txt ({len(script)} 字)")
    print(f"  标题: {meta['title']}")
    print(f"  标签: {', '.join(meta['tags'])}")


if __name__ == "__main__":
    main()
