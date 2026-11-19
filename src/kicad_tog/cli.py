"""Command-line interface for kicad-tog."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from .board import Board
from .check import check
from .report import failed, plot, to_json, to_text


def _nets(value: str) -> list[str]:
    nets = [net.strip() for net in value.split(",") if net.strip()]
    if not nets:
        raise argparse.ArgumentTypeError("provide at least one net name")
    return nets


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("k must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("k must be greater than zero")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kicad-tog",
        description="Check signal traces for gaps in their reference plane.",
    )
    parser.add_argument("board", type=Path, help="input KiCad PCB file")
    parser.add_argument("--nets", required=True, type=_nets, help="comma-separated net names")
    parser.add_argument("--ref", required=True, help="reference copper layer")
    parser.add_argument(
        "-k", "--k", type=_positive_float, default=3.0,
        help="return-current margin factor (default: 3.0)",
    )
    parser.add_argument("--json", type=Path, help="write a JSON report")
    parser.add_argument("--png", type=Path, help="write a plot as PNG")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        board = Board.load(args.board)

        unknown_nets = sorted(set(args.nets) - {segment.net for segment in board.segments})
        if unknown_nets:
            raise ValueError(f"unknown net(s): {', '.join(unknown_nets)}")

        # check() raises ValueError when the reference layer has no copper.
        violations = check(board, args.nets, args.ref, k=args.k)

        # The same header fields go into the text and the JSON report.
        meta = {
            "board_name": args.board.name,  # file name only: same output on every machine
            "ref_layer": args.ref,
            "nets": args.nets,
            "k": args.k,
        }
        report = to_text(violations, **meta)

        if args.json is not None:
            args.json.write_text(to_json(violations, **meta), encoding="utf-8")

        if args.png is not None:
            figure = plot(board, violations, nets=args.nets, ref_layer=args.ref)
            figure.savefig(args.png, dpi=150, bbox_inches="tight")

    except (OSError, ValueError) as exc:
        # Bad input, not a failed check: exit 2, one line, no traceback.
        print(f"kicad-tog: error: {args.board.name}: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(report)
    return 1 if failed(violations) else 0
