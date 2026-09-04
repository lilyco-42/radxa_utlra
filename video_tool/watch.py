from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .config import Config
from .editor import discover_media, edit
from .upload import Uploader


def _gate_open(cfg: Config) -> bool:
    """资源协调闸门: 未配置 gate_command 时恒开放; 配置后执行该命令,
    退出码 0=放行, 非 0=暂停(如 MC 有玩家在线). 命令异常时稳妥放行."""
    cmd = (cfg.gate_command or "").strip()
    if not cmd:
        return True
    try:
        rc = subprocess.run(
            cmd,
            shell=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
    except Exception as exc:  # 闸门脚本坏了不能把整条流水线卡死
        print(f"gate_command 执行异常 ({exc}); 按开放处理")
        return True
    # 0 = 放行; 1 (及其他非 0) = 暂停(如 MC 有玩家);
    # 126/127 = 命令找不到/不可执行, 视为闸门损坏 -> 稳妥放行, 不卡死流水线
    return rc == 0 or rc in (126, 127)


def _file_key(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _stable(path: Path, seconds: int) -> bool:
    before = _file_key(path)
    time.sleep(max(1, seconds))
    return before == _file_key(path)


def _save_state(state_file: Path, state: dict[str, str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_state(state_file: Path) -> dict[str, str]:
    if not state_file.is_file():
        return {}
    try:
        loaded = json.loads(state_file.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def watch(cfg: Config, once: bool = False, interval: int | None = None) -> None:
    interval = interval or cfg.interval
    input_dir = Path(cfg.input_dir).expanduser()
    output_dir = Path(cfg.output_dir).expanduser()
    done_dir = Path(cfg.done_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    state_file = output_dir / ".radxa-video-state.json"
    state = _load_state(state_file)

    while True:
        sources = discover_media(input_dir, cfg.extensions)
        pending = [
            path for path in sources if state.get(str(path)) != _file_key(path)
        ]

        if not _gate_open(cfg):
            print("闸门关闭 (如 MC 有玩家在线), 本轮暂停视频处理")
            if once:
                break
            time.sleep(interval)
            continue

        if pending:
            print(f"Found {len(pending)} new media file(s)")
            stable = True
            for path in pending:
                if not _stable(path, 3):
                    print(f"Still writing: {path}")
                    stable = False
                    break

            if stable:
                try:
                    result = edit(cfg, files=pending)
                    uploader = Uploader(cfg)
                    uploader.upload(result)
                    srt = result.with_suffix(".srt")
                    if srt.is_file():
                        uploader.upload(srt)

                    for path in pending:
                        state[str(path)] = _file_key(path)
                    _save_state(state_file, state)

                    if not cfg.keep_raw:
                        done_dir.mkdir(parents=True, exist_ok=True)
                        for path in pending:
                            destination = done_dir / path.name
                            if destination.exists():
                                destination = (
                                    done_dir
                                    / f"{path.stem}_{int(time.time())}{path.suffix}"
                                )
                            shutil.move(str(path), str(destination))
                except Exception as exc:  # keep the watcher alive on partial failures
                    print(f"Processing failed: {exc}")

        if once:
            break
        time.sleep(interval)
