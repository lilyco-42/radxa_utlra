from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import Config


def _run(command: list[str], env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, env=env, check=True)


def _resolve_gh_tag(repo: str, tag: str, env: dict[str, str]) -> str:
    if tag != "latest":
        return tag
    result = subprocess.run(
        ["gh", "release", "view", "latest", "--repo", repo, "--json", "tagName",
         "--jq", ".tagName"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def _http_upload(path: Path, url: str) -> None:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is required for http upload targets") from exc

    token = os.environ.get("RADXA_UPLOAD_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with path.open("rb") as fh:
        response = requests.post(
            url,
            files={"file": (path.name, fh)},
            headers=headers,
            timeout=300,
        )
    response.raise_for_status()


class Uploader:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def upload(self, path: str | Path) -> list[bool]:
        source = Path(path).expanduser()
        if not source.is_file():
            raise FileNotFoundError(f"Upload source not found: {source}")

        targets = self.cfg.upload_targets or []
        if not targets:
            print(f"No upload targets configured; skipping {source}")
            return []

        results: list[bool] = []
        for target in targets:
            results.append(self._upload_one(source, target))
        return results

    def _upload_one(self, source: Path, target: str) -> bool:
        if target.startswith("rclone:"):
            remote = target[len("rclone:"):]
            _run(["rclone", "copy", str(source), remote])
            return True

        if target.startswith("gh:"):
            self._upload_github(source, target[len("gh:"):])
            return True

        if target.startswith("cp:"):
            destination = Path(target[len("cp:"):]).expanduser()
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination / source.name)
            return True

        if target.startswith(("http://", "https://")):
            _http_upload(source, target)
            return True

        destination = Path(target).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination / source.name)
        return True

    def _upload_github(self, source: Path, spec: str) -> None:
        if ":" in spec:
            repo, tag = spec.split(":", 1)
        else:
            repo, tag = spec, "latest"

        env = os.environ.copy()
        env.setdefault("GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
        tag = _resolve_gh_tag(repo, tag, env)
        _run([
            "gh", "release", "upload", tag, str(source),
            "--repo", repo, "--clobber",
        ], env=env)
