#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""④ 发布 —— 支持多平台。

后端（config.yaml 的 publish.backend）：
  biliup  = 只发 B站（arm64 原生二进制，轻量）
  sau     = 多平台（social-auto-upload，浏览器自动化）
  both    = 两个都跑

用法:
    python publish.py --video final.mp4 --meta meta.json [--cover cover.png] [--dry-run]
    python publish.py --video final.mp4 --meta meta.json --backend sau
    python publish.py --video final.mp4 --meta meta.json --only douyin,xiaohongshu

首次使用前需登录（各自一次）：
    B站:   ~/biliup/biliupR-v1.2.4-aarch64-linux/biliup login
    多平台: ~/sau/.venv/bin/sau <平台> login --account <账号名>
"""
import argparse
import json
import os
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_meta(meta_path):
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
    return {
        "title": (meta.get("title") or "未命名")[:80],
        "desc": meta.get("desc") or "",
        "tags": tags,
    }


# ── B站（biliup）──────────────────────────────────────────
def publish_biliup(pub, video, meta, cover, dry_run):
    binp = os.path.expanduser(pub.get("biliup_bin", ""))
    cookie = os.path.expanduser(pub.get("cookie_file", ""))

    if not os.path.exists(binp):
        print(f"  ✗ biliup 不存在: {binp}")
        return False
    if not dry_run and not os.path.exists(cookie):
        print(f"  ✗ 未登录（缺 {cookie}）")
        print(f"    请执行: {binp} login")
        return False

    cmd = [
        binp, "--user-cookie", cookie, "upload", video,
        "--title", meta["title"],
        "--desc", meta["desc"],
        "--tag", ",".join(meta["tags"]),
        "--tid", str(pub.get("tid", 171)),
        "--copyright", str(pub.get("copyright", 1)),
        "--no-reprint", str(pub.get("no_reprint", 1)),
    ]
    if cover and os.path.exists(cover):
        cmd += ["--cover", cover]
    if pub.get("proxy"):
        cmd = [cmd[0], "-p", pub["proxy"]] + cmd[1:]

    print(f"  [bilibili] {meta['title']}")
    if dry_run:
        print(f"    (dry-run) {' '.join(cmd)}")
        return True

    r = subprocess.run(cmd, cwd=os.path.expanduser("~/vp"))
    if r.returncode == 0:
        print("  ✓ bilibili 已提交")
        return True
    print(f"  ✗ bilibili 失败 (exit {r.returncode})")
    return False


# ── 多平台（social-auto-upload）───────────────────────────

# 平台"发布成功"标志：sau 打印这些文案后即视为已发出，
# 不等 Playwright driver 自然退出（它会挂住不返回）。
SAU_SUCCESS_MARKERS = (
    "视频发布成功",
    "发布成功",
    "🥳",
    "upload success",
    "published successfully",
)

# 登录态失效标志：看到就立即失败，不要等满超时
SAU_FAIL_MARKERS = (
    "redirectReason=401",
    "账号未登录",
    "登录态失效",
    "cookie is missing or expired",
    "cookie 已失效",
    "missing or expired",
)


def run_sau_command(cmd, cwd, platform, timeout=300):
    """运行一条 sau 命令，带：
      - 独立进程组（便于整组清理）
      - 流式转发输出
      - 看见成功标志后 3s 收尾，仍不退则杀进程组（按成功处理）
      - 看见登录失效标志立即失败
      - 硬上限 timeout 秒
    返回 (rc, succeeded)。rc==0 表示成功。
    """
    import signal
    import select
    import time as _time

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,      # 独立进程组
        text=True, bufsize=1,
        encoding="utf-8", errors="replace",   # 平台输出含非 UTF-8 字节时不崩
    )
    # 进程组 ID：Unix 上 start_new_session 让子进程成为组长，
    # 所以 pgid == pid。getpgid 在 Windows 上不存在，做个防御。
    try:
        pgid = os.getpgid(proc.pid)
    except (AttributeError, OSError):
        pgid = proc.pid

    def _kill_group():
        try:
            if hasattr(os, "killpg"):
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            return
        _time.sleep(1)
        try:
            if hasattr(os, "killpg"):
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    started = _time.time()
    saw_success = False
    saw_fail = False
    success_at = None
    tail = []

    try:
        while True:
            # 硬超时
            if _time.time() - started > timeout:
                print(f"    ⏱ {platform} 超过 {timeout}s 上限，终止")
                _kill_group()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return (0 if saw_success else 124), saw_success

            # 成功后再给 3s 收尾窗口
            if success_at is not None and _time.time() - success_at > 3:
                print(f"    （{platform} 已成功，收尾进程组）")
                _kill_group()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return 0, True

            _can_select = True
            try:
                rlist, _, _ = select.select([proc.stdout], [], [], 0.5)
            except (OSError, ValueError):
                # Windows 上 select 不能用于管道（板子是 Linux，这里只是本地可测）
                _can_select = False
                rlist = []

            if not _can_select:
                # 退化路径：阻塞读一行（仍受上面的硬超时保护，但粒度较粗）
                line = proc.stdout.readline()
                if not line:
                    break
                rlist = [proc.stdout]
                _pending = line
            else:
                _pending = None

            if rlist:
                line = _pending if _pending is not None else proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip("\n")
                print(f"      {line}")
                tail.append(line)
                if len(tail) > 40:
                    tail.pop(0)

                if any(m in line for m in SAU_SUCCESS_MARKERS) and not saw_success:
                    saw_success = True
                    success_at = _time.time()
                    print(f"    ✓ {platform} 检测到发布成功标志")

                if any(m in line for m in SAU_FAIL_MARKERS):
                    saw_fail = True
                    print(f"    ✗ {platform} 登录态失效，快速失败")
                    _kill_group()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    return 2, False

            if proc.poll() is not None:
                # 读干净剩余输出
                rest = proc.stdout.read() or ""
                for line in rest.splitlines():
                    print(f"      {line}")
                break
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass

    rc = proc.returncode or 0
    if saw_success:
        return 0, True
    if saw_fail:
        return 2, False
    # 进程自己退了且没看到失败标志 —— 按 rc 判断
    return rc, rc == 0


def publish_sau(pub, video, meta, cover, dry_run, only=None):
    sau = os.path.expanduser(pub.get("sau_bin", ""))
    root = os.path.expanduser(pub.get("sau_root", "~/sau"))

    if not os.path.exists(sau):
        print(f"  ✗ sau 不存在: {sau}")
        return False

    plats = [p for p in (pub.get("platforms") or []) if p.get("enabled")]
    if only:
        want = {x.strip() for x in only.split(",") if x.strip()}
        plats = [p for p in plats if p["name"] in want]

    if not plats:
        print("  （没有启用的平台 —— 在 config.yaml 里把 platforms 的 enabled 改成 true）")
        return True

    per_platform_timeout = int(pub.get("sau_timeout", 300))
    ok = 0
    for p in plats:
        name, acct = p["name"], p.get("account", "")
        if not acct:
            print(f"  ✗ {name}: 没填 account")
            continue

        cmd = [
            sau, name, "upload-video",
            "--account", acct,
            "--file", os.path.abspath(video),
            "--title", meta["title"],
            "--desc", meta["desc"],
            "--tags", ",".join(meta["tags"]),
        ]
        if cover and os.path.exists(cover) and name in ("douyin", "kuaishou"):
            cmd += ["--thumbnail", cover]

        print(f"  [{name}] {meta['title']}")
        if dry_run:
            print(f"    (dry-run) {' '.join(cmd)}")
            ok += 1
            continue

        rc, succeeded = run_sau_command(cmd, root, name, timeout=per_platform_timeout)
        if succeeded:
            print(f"  ✓ {name} 已提交")
            ok += 1
        else:
            # 单个平台失败不影响其他平台
            print(f"  ✗ {name} 失败 (exit {rc})")

    print(f"  多平台结果: {ok}/{len(plats)} 成功")
    return ok > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--cover", default="")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--backend", default="", help="覆盖 config 里的 backend")
    ap.add_argument("--only", default="", help="只发这些平台（逗号分隔），仅 sau 有效")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"视频不存在: {args.video}")

    cfg = load_cfg(args.config)
    pub = cfg.get("publish") or {}
    meta = read_meta(args.meta)
    backend = (args.backend or pub.get("backend") or "biliup").lower()

    print("=" * 60)
    print(f"标题: {meta['title']}")
    print(f"标签: {', '.join(meta['tags'])}")
    print(f"视频: {args.video} ({os.path.getsize(args.video)//1024} KB)")
    print(f"后端: {backend}")
    print("=" * 60)

    results = {}
    if backend in ("biliup", "both"):
        results["biliup"] = publish_biliup(pub, args.video, meta, args.cover, args.dry_run)
    if backend in ("sau", "both"):
        results["sau"] = publish_sau(pub, args.video, meta, args.cover, args.dry_run, args.only or None)

    print("=" * 60)
    for k, v in results.items():
        print(f"  {k}: {'OK' if v else 'FAILED'}")

    # 任一后端成功就算成功（多平台本来就是尽力而为）
    sys.exit(0 if any(results.values()) else 1)


if __name__ == "__main__":
    main()
