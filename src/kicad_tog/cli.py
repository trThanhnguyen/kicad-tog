"""Command-line interface for kicad-tog."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from .board import Board
from .check import check, reference_map
from .project import select_nets
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
    parser.add_argument(
        "--nets", type=_nets,
        help="comma-separated net names or wildcards like '/*PCIE*' (default: nets in "
        "non-Default net classes of the .kicad_pro, else differential-pair names)",
    )
    parser.add_argument(
        "--ref",
        help="reference copper layer (default: for each trace, the nearest plane layer "
        "above and below it; a plane = fills covering >= 50%% of the board)",
    )
    parser.add_argument(
        "--layer",
        help="check only segments on this signal layer (needed on real boards, "
        "where nets change layers through vias; default: every layer)",
    )
    parser.add_argument(
        "-k", "--k", type=_positive_float, default=3.0,
        help="return-current margin factor (default: 3.0)",
    )
    parser.add_argument(
        "--json", type=Path,
        help="where to write the JSON report (default: out/<board>.json)",
    )
    parser.add_argument(
        "--png", type=Path,
        help="where to write the plot (default: out/<board>.png; automatic "
        "references: one file per plane, out.png -> out_In1.Cu.png, ...)",
    )
    return parser


def plane_png(path: Path, plane: str) -> Path:
    """out.png + In1.Cu -> out_In1.Cu.png"""
    return path.with_name(f"{path.stem}_{plane}{path.suffix}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Reports are always written: to out/ under the working directory unless
    # given. Not next to the board: KiCad's demos sit in read-only Program Files.
    json_path = args.json or Path("out") / f"{args.board.stem}.json"
    png_path = args.png or Path("out") / f"{args.board.stem}.png"

    try:
        board = Board.load(args.board)

        # --nets, else the project's net classes, else pair names; raises
        # ValueError on an unknown net or when nothing is selected.
        nets, nets_reason = select_nets(
            {segment.net for segment in board.segments},
            args.nets,
            args.board.with_suffix(".kicad_pro"),
        )

        # Same reason as unknown nets: a wrong layer would check nothing and pass.
        if args.layer is not None and not any(
            segment.net in nets and segment.layer == args.layer
            for segment in board.segments
        ):
            raise ValueError(f"no segments of {', '.join(nets)} on {args.layer}")

        # check() raises ValueError when the reference layer has no copper,
        # when the signal layer is the reference layer, or (auto) when the
        # board has no stackup.
        violations = check(board, nets, args.ref, k=args.k, signal_layer=args.layer)

        # Automatic references: plane -> the signal layers that use it.
        planes = {} if args.ref is not None else reference_map(board, nets, args.layer)

        # The same header fields go into the text and the JSON report.
        meta = {
            "board_name": args.board.name,  # file name only: same output on every machine
            "ref_layer": args.ref,
            "nets": nets,
            "k": args.k,
            "signal_layer": args.layer,
            "planes": list(planes),
            "nets_reason": nets_reason,
        }
        report = to_text(violations, **meta)

        # A missing folder is created; one that can't be (a file in the way,
        # no permission) is an OSError: exit 2 below.
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(to_json(violations, **meta), encoding="utf-8")

        png_path.parent.mkdir(parents=True, exist_ok=True)
        if args.ref is not None:
            figure = plot(
                board, violations, nets=nets, ref_layer=args.ref, signal_layer=args.layer
            )
            figure.savefig(png_path, dpi=150, bbox_inches="tight")
        for plane, layers in planes.items():
            figure = plot(
                board,
                [v for v in violations if v.ref_layer == plane],
                nets=nets,
                ref_layer=plane,
                signal_layer=args.layer,
                trace_layers=layers,
            )
            figure.savefig(plane_png(png_path, plane), dpi=150, bbox_inches="tight")

    except (OSError, ValueError) as exc:
        # Bad input, not a failed check: exit 2, one line, no traceback.
        print(f"kicad-tog: error: {args.board.name}: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(report)
    return 1 if failed(violations) else 0
