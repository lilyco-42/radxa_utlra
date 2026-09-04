from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_tool.config import Config
from video_tool.editor import discover_media, _audio_filter
from video_tool.transcribe import format_timestamp
from video_tool.watch import _gate_open
from video_tool.cut_fillers import _normalize_token, _build_cuts, _invert, Word, ZH_FILLERS, EN_FILLERS


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


class CutFillersTests(unittest.TestCase):
    def test_normalize_token_strips_punctuation(self) -> None:
        self.assertEqual(_normalize_token("Hello,"), "hello")
        self.assertEqual(_normalize_token("...嗯！"), "嗯")
        self.assertEqual(_normalize_token("  UHM  "), "uhm")

    def test_chinese_filler_detected(self) -> None:
        self.assertIn("嗯", ZH_FILLERS)
        self.assertIn("那个", ZH_FILLERS)
        self.assertIn("就是", ZH_FILLERS)

    def test_english_filler_detected(self) -> None:
        self.assertIn("uhm", EN_FILLERS)
        self.assertIn("um", EN_FILLERS)

    def test_build_cuts_detects_silence(self) -> None:
        words = [
            Word("hello", 0.0, 1.0),
            Word("world", 2.0, 3.0),  # 1.0s gap > 0.5 threshold
        ]
        cuts = _build_cuts(words, 3.0, EN_FILLERS, 0.5, 0.08)
        self.assertTrue(any(r == "silence" for _, _, r in cuts))

    def test_build_cuts_detects_filler(self) -> None:
        words = [
            Word("hello", 0.0, 1.0),
            Word("嗯", 1.5, 1.8),   # filler
            Word("world", 2.0, 3.0),
        ]
        cuts = _build_cuts(words, 3.0, ZH_FILLERS, 0.5, 0.08)
        self.assertTrue(any("filler" in r for _, _, r in cuts))

    def test_invert_produces_keeps(self) -> None:
        cuts = [(1.0, 2.0, "silence")]
        keeps = _invert(cuts, 3.0)
        self.assertEqual(len(keeps), 2)
        self.assertEqual(keeps[0], (0.0, 1.0))
        self.assertEqual(keeps[1], (2.0, 3.0))

    def test_invert_empty_cuts_keeps_all(self) -> None:
        keeps = _invert([], 5.0)
        self.assertEqual(keeps, [(0.0, 5.0)])

    def test_invert_all_cut_returns_empty(self) -> None:
        cuts = [(0.0, 5.0, "silence")]
        keeps = _invert(cuts, 5.0)
        self.assertEqual(keeps, [])


class AudioFilterTests(unittest.TestCase):
    def test_no_filter_when_disabled(self) -> None:
        cfg = Config.load()
        cfg.denoise = False
        cfg.loudnorm = False
        self.assertIsNone(_audio_filter(cfg))

    def test_denoise_only(self) -> None:
        cfg = Config.load()
        cfg.denoise = True
        cfg.loudnorm = False
        af = _audio_filter(cfg)
        self.assertIsNotNone(af)
        self.assertIn("afftdn", af)

    def test_loudnorm_only(self) -> None:
        cfg = Config.load()
        cfg.denoise = False
        cfg.loudnorm = True
        af = _audio_filter(cfg)
        self.assertIsNotNone(af)
        self.assertIn("loudnorm", af)

    def test_both_filters(self) -> None:
        cfg = Config.load()
        cfg.denoise = True
        cfg.loudnorm = True
        af = _audio_filter(cfg)
        self.assertIsNotNone(af)
        self.assertIn("afftdn", af)
        self.assertIn("loudnorm", af)


class NewConfigFieldsTests(unittest.TestCase):
    def test_remove_fillers_default_false(self) -> None:
        cfg = Config.load()
        self.assertFalse(cfg.remove_fillers)

    def test_max_silence_default(self) -> None:
        cfg = Config.load()
        self.assertEqual(cfg.max_silence, 0.5)

    def test_filler_pad_default(self) -> None:
        cfg = Config.load()
        self.assertEqual(cfg.filler_pad, 0.08)

    def test_denoise_default_false(self) -> None:
        cfg = Config.load()
        self.assertFalse(cfg.denoise)

    def test_loudnorm_default_false(self) -> None:
        cfg = Config.load()
        self.assertFalse(cfg.loudnorm)

    def test_yaml_loads_new_fields(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                "remove_fillers: true\nmax_silence: 0.3\ndenoise: true\nloudnorm: true\n",
                encoding="utf-8",
            )
            cfg = Config.load(path)
            self.assertTrue(cfg.remove_fillers)
            self.assertEqual(cfg.max_silence, 0.3)
            self.assertTrue(cfg.denoise)
            self.assertTrue(cfg.loudnorm)


if __name__ == "__main__":
    unittest.main()
