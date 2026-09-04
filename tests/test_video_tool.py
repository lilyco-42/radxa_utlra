from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_tool.config import Config
from video_tool.editor import discover_media
from video_tool.transcribe import format_timestamp
from video_tool.watch import _gate_open


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = Config.load()
        self.assertEqual(cfg.radxa_host, "lain42.top")
        self.assertEqual(cfg.radxa_key, "~/.ssh/lain42.pem")
        self.assertEqual(cfg.width, 1080)

    def test_yaml_config(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                "width: 1280\nheight: 720\nupload_targets: cp:/tmp/out\n",
                encoding="utf-8",
            )
            cfg = Config.load(path)
            self.assertEqual(cfg.width, 1280)
            self.assertEqual(cfg.height, 720)
            self.assertEqual(cfg.upload_targets, ["cp:/tmp/out"])


class EditorTests(unittest.TestCase):
    def test_discover_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mp4").write_bytes(b"video")
            (root / "b.JPG").write_bytes(b"image")
            (root / "notes.txt").write_text("skip", encoding="utf-8")

            found = discover_media(root, [".mp4", ".jpg"])
            self.assertEqual([p.name for p in found], ["a.mp4", "b.JPG"])


class TranscribeTests(unittest.TestCase):
    def test_format_timestamp(self) -> None:
        self.assertEqual(format_timestamp(0), "00:00:00,000")
        self.assertEqual(format_timestamp(3661.5), "01:01:01,500")


class GateTests(unittest.TestCase):
    def test_unset_gate_is_open(self) -> None:
        self.assertTrue(_gate_open(Config.load()))

    def test_shell_true_is_open(self) -> None:
        cfg = Config.load()
        cfg.gate_command = "true"
        self.assertTrue(_gate_open(cfg))

    def test_shell_false_is_closed(self) -> None:
        cfg = Config.load()
        cfg.gate_command = "false"
        self.assertFalse(_gate_open(cfg))

    def test_nonzero_exit_is_closed(self) -> None:
        cfg = Config.load()
        cfg.gate_command = "exit 3"
        self.assertFalse(_gate_open(cfg))

    def test_broken_gate_falls_open(self) -> None:
        # 126/127 = 命令找不到/不可执行, 视为闸门损坏 -> 放行, 不卡死流水线
        cfg = Config.load()
        cfg.gate_command = "exit 127"
        self.assertTrue(_gate_open(cfg))


if __name__ == "__main__":
    unittest.main()
