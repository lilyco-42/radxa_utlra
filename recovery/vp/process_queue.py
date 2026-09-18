#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""process_queue.py — 从 vp 队列取下一个任务，喂给 run_pipeline.sh，推进索引。

用法:
    python3 process_queue.py                # 处理队列中的下一个任务
    python3 process_queue.py --dry-run      # 只显示将要做什么，不执行
    python3 process_queue.py --status       # 显示队列进度
    python3 process_queue.py --reset        # 重置 next_index 为 0
    python3 process_queue.py --skip         # 跳过当前任务（标记 failed 并推进）

设计原则:
  - 每次只处理 1 个任务（A7A 单核渲染，串行安全）
  - 失败的任务标记 failed 并推进，不卡住队列
  - state.json 原子写入（temp + rename）
  - publish 由 state.json 的 publish 字段控制
  - 队列耗尽时正常退出 0
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/home/radxa"))
VP = HOME / "vp"
QUEUE_DIR = VP / "queue"
STATE_FILE = QUEUE_DIR / "state.json"
LOGDIR = VP / "logs"
LOGDIR.mkdir(parents=True, exist_ok=True)


def load_state():
    if not STATE_FILE.exists():
        print(f"[ERROR] state.json 不存在: {STATE_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_queue(queue_file: str):
    qpath = QUEUE_DIR / queue_file
    if not qpath.exists():
        print(f"[ERROR] 队列文件不存在: {qpath}", file=sys.stderr)
        sys.exit(1)
    with open(qpath, encoding="utf-8") as f:
        return json.load(f)


def save_state_atomic(state: dict):
    """原子写入 state.json（temp + rename）"""
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, STATE_FILE)


def save_queue_atomic(queue_file: str, queue: dict):
    """原子写入队列文件"""
    qpath = QUEUE_DIR / queue_file
    tmp = qpath.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, qpath)


def show_status(state, queue):
    """显示队列进度摘要"""
    tasks = queue.get("tasks", [])
    total = len(tasks)
    idx = state.get("next_index", 0)
    done = sum(1 for t in tasks if t.get("status") == "done")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    queued = sum(1 for t in tasks if t.get("status") == "queued")
    running = sum(1 for t in tasks if t.get("status") == "running")

    print(f"队列文件:   {state.get('queue_file', '?')}")
    print(f"总任务数:   {total}")
    print(f"下一索引:   {idx}")
    print(f"已完成:     {done}")
    print(f"已失败:     {failed}")
    print(f"排队中:     {queued}")
    print(f"进行中:     {running}")
    print(f"发布开关:   {'开启' if state.get('publish') else '关闭'}")
    print(f"自动启动:   {'是' if state.get('auto_start') else '否'}")

    if idx < total:
        t = tasks[idx]
        print(f"\n下一个任务: [{idx}] {t.get('id', '?')}")
        print(f"  主题:   {t.get('topic', '?')}")
        print(f"  类别:   {t.get('category', '?')}")
        print(f"  来源:   {t.get('source_type', '?')}")
    else:
        print("\n队列已耗尽，所有任务已处理。")

    # 最近 5 条结果
    recent = [(i, t) for i, t in enumerate(tasks) if t.get("status") in ("done", "failed")]
    if recent:
        print("\n最近结果:")
        for i, t in recent[-5:]:
            st = t.get("status", "?")
            mark = "OK" if st == "done" else "FAIL"
            print(f"  [{i}] {mark}  {t.get('topic', '?')[:40]}")


def run_pipeline(topic: str, publish: bool, dry_run: bool = False) -> int:
    """调用 run_pipeline.sh 处理一个话题"""
    env = os.environ.copy()
    env["PUBLISH"] = "1" if publish else "0"
    env["CLEANUP"] = "1"

    cmd = ["bash", str(VP / "run_pipeline.sh"), topic]

    if dry_run:
        print(f"[DRY-RUN] 将执行: {' '.join(cmd)}")
        print(f"[DRY-RUN] PUBLISH={env['PUBLISH']}")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    log_file = LOGDIR / f"queue-{ts}.log"

    print(f"启动流水线: {topic[:50]}")
    print(f"日志: {log_file}")

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(f"--- queue task: {topic} ---\n")
        lf.flush()
        result = subprocess.run(
            cmd,
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            timeout=2100,  # 35 分钟上限（script 5min + tts 3min + render 15min + publish 15min）
            cwd=str(VP),
        )

    return result.returncode


def process_one(state, queue, dry_run=False, skip=False):
    """处理队列中的下一个任务"""
    tasks = queue.get("tasks", [])
    total = len(tasks)
    idx = state.get("next_index", 0)

    if idx >= total:
        print("队列已耗尽，没有更多任务。")
        return 0

    task = tasks[idx]
    task_id = task.get("id", f"task-{idx}")
    topic = task.get("topic", "")
    publish = state.get("publish", False)

    if not topic:
        print(f"[WARN] 任务 [{idx}] {task_id} 没有主题，跳过")
        task["status"] = "failed"
        task["error"] = "empty_topic"
        task["processed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["next_index"] = idx + 1
        save_queue_atomic(state["queue_file"], queue)
        save_state_atomic(state)
        return 0

    if skip:
        print(f"跳过任务 [{idx}] {task_id}: {topic[:40]}")
        task["status"] = "failed"
        task["error"] = "skipped_by_user"
        task["processed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["next_index"] = idx + 1
        save_queue_atomic(state["queue_file"], queue)
        save_state_atomic(state)
        return 0

    print(f"处理任务 [{idx}/{total}] {task_id}")
    print(f"  主题: {topic}")
    print(f"  类别: {task.get('category', '?')}")
    print(f"  发布: {'是' if publish else '否'}")

    # 标记为 running
    task["status"] = "running"
    task["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_queue_atomic(state["queue_file"], queue)

    if dry_run:
        print("[DRY-RUN] 不执行流水线，只标记状态")
        task["status"] = "queued"  # 回退
        return 0

    rc = run_pipeline(topic, publish, dry_run=False)

    task["processed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if rc == 0:
        task["status"] = "done"
        print(f"  => 成功")
    elif rc == 75:
        # flock 冲突 = 有另一个实例在跑
        print(f"  => 跳过（已有流水线实例在运行）")
        task["status"] = "queued"  # 回退，下次再试
        return 75
    elif rc == 124:
        task["status"] = "failed"
        task["error"] = "timeout"
        print(f"  => 超时失败")
    else:
        task["status"] = "failed"
        task["error"] = f"exit_code_{rc}"
        print(f"  => 失败 (rc={rc})")

    # 推进索引（75 = flock 冲突不推进）
    if rc != 75:
        state["next_index"] = idx + 1
        state["last_processed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["last_rc"] = rc
        save_queue_atomic(state["queue_file"], queue)
        save_state_atomic(state)

    return rc


def main():
    parser = argparse.ArgumentParser(description="vp 队列处理器")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要做什么")
    parser.add_argument("--status", action="store_true", help="显示队列进度")
    parser.add_argument("--reset", action="store_true", help="重置 next_index 为 0")
    parser.add_argument("--skip", action="store_true", help="跳过当前任务")
    parser.add_argument("--publish", action="store_true", help="覆盖 publish 为 true（仅本次）")
    parser.add_argument("--no-publish", action="store_true", help="覆盖 publish 为 false（仅本次）")
    args = parser.parse_args()

    state = load_state()
    queue = load_queue(state.get("queue_file", "lyco-rust-20260917.json"))

    if args.status:
        show_status(state, queue)
        return

    if args.reset:
        state["next_index"] = 0
        # 重置所有 running 回 queued
        for t in queue.get("tasks", []):
            if t.get("status") == "running":
                t["status"] = "queued"
        save_queue_atomic(state["queue_file"], queue)
        save_state_atomic(state)
        print("队列已重置: next_index=0, running→queued")
        show_status(state, queue)
        return

    # 命令行覆盖 publish
    if args.publish:
        state["publish"] = True
    elif args.no_publish:
        state["publish"] = False

    rc = process_one(state, queue, dry_run=args.dry_run, skip=args.skip)
    sys.exit(rc)


if __name__ == "__main__":
    main()
