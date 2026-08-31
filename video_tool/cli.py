from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import Config
from .editor import edit
from .upload import Uploader
from .watch import watch


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=os.environ.get("RADXA_VIDEO_CONFIG"),
        help="Path to config.yaml (default: $RADXA_VIDEO_CONFIG or built-in defaults)",
    )


def _cmd_edit(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    result = edit(
        cfg,
        input_dir=args.input,
        output_dir=args.output,
    )
    print(f"edited: {result}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    if args.input:
        cfg.input_dir = Path(args.input)
    if args.output:
        cfg.output_dir = Path(args.output)
    watch(cfg, once=args.once, interval=args.interval)
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    if args.target:
        cfg.upload_targets = args.target

    sources: list[Path] = []
    for item in args.file or []:
        sources.append(Path(item).expanduser())
    for folder in args.directory or []:
        root = Path(folder).expanduser()
        if not root.is_dir():
            raise SystemExit(f"Directory not found: {root}")
        sources.extend(p for p in root.rglob("*") if p.is_file())

    if not sources:
        raise SystemExit("Nothing to upload; pass --file or --directory")

    uploader = Uploader(cfg)
    for source in sources:
        uploader.upload(source)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radxa-video",
        description="Automatic video editing and upload for Radxa",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    edit_parser = sub.add_parser("edit", help="Auto-edit media into one video")
    _add_common(edit_parser)
    edit_parser.add_argument("-i", "--input", help="Input directory")
    edit_parser.add_argument("-o", "--output", help="Output directory")
    edit_parser.set_defaults(func=_cmd_edit)

    watch_parser = sub.add_parser("watch", help="Watch input dir and auto edit/upload")
    _add_common(watch_parser)
    watch_parser.add_argument("-i", "--input", help="Input directory")
    watch_parser.add_argument("-o", "--output", help="Output directory")
    watch_parser.add_argument("--once", action="store_true", help="Process once and exit")
    watch_parser.add_argument("--interval", type=int, help="Poll interval seconds")
    watch_parser.set_defaults(func=_cmd_watch)

    upload_parser = sub.add_parser("upload", help="Upload files to configured targets")
    _add_common(upload_parser)
    upload_parser.add_argument("-f", "--file", action="append", help="File to upload")
    upload_parser.add_argument("-d", "--directory", action="append", help="Upload all files")
    upload_parser.add_argument("--target", action="append", help="Override upload target")
    upload_parser.set_defaults(func=_cmd_upload)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
