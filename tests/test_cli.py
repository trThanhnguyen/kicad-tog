"""Tests for cli.py (P5): the command-line front door.

cli.py holds no checking logic. It turns argv into calls
(Board.load -> check -> report) and turns the result into an exit code:

    0  ran, no errors (warnings and info do not fail)
    1  ran, at least one error (a split)
    2  could not run: bad arguments, unreadable board, nothing to check

CI (P6) only sees the exit code, so these numbers are the contract.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from matplotlib import image as mpimg
from matplotlib import pyplot as plt

from kicad_tog.board import Board
from kicad_tog.check import check
from kicad_tog.cli import build_parser, main
from kicad_tog.report import to_json, to_text

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "fixtures"


def board_path(name: str) -> str:
    return str(FIXTURES / f"{name}.kicad_pcb")


def run(name: str, *extra: str) -> int:
    return main([board_path(name), "--nets", "SIG", "--ref", "In1.Cu", *extra])


# --- argument parsing ------------------------------------------------------


def test_parser_defaults():
    args = build_parser().parse_args(["b.kicad_pcb", "--nets", "SIG", "--ref", "In1.Cu"])
    assert args.board == Path("b.kicad_pcb") or str(args.board) == "b.kicad_pcb"
    assert args.nets == ["SIG"]
    assert args.ref == "In1.Cu"
    assert args.k == 3.0
    assert args.json is None
    assert args.png is None


def test_nets_are_comma_separated_and_stripped():
    args = build_parser().parse_args(["b.kicad_pcb", "--nets", "SIG, CLK", "--ref", "In1.Cu"])
    assert args.nets == ["SIG", "CLK"]


def test_k_is_a_float():
    args = build_parser().parse_args(
        ["b.kicad_pcb", "--nets", "SIG", "--ref", "In1.Cu", "--k", "1.5"]
    )
    assert args.k == 1.5


@pytest.mark.parametrize(
    "argv",
    [
        ["--nets", "SIG", "--ref", "In1.Cu"],  # no board
        ["b.kicad_pcb", "--nets", "SIG", "--ref", "In1.Cu", "--k", "abc"],
        ["b.kicad_pcb", "--nets", "SIG", "--ref", "In1.Cu", "--k", "-1"],
        ["b.kicad_pcb", "--nets", ",", "--ref", "In1.Cu"],  # no net names at all
    ],
)
def test_bad_arguments_exit_2(argv, capsys):
    # argparse reports usage errors by raising SystemExit(2).
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert capsys.readouterr().err  # it said why, on stderr


# --- exit codes on the fixtures --------------------------------------------


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("continuous", 0),  # info only
        ("split_crossed", 1),  # one split = error
        ("edge_overhang", 0),  # warning only: Option C, does not fail
    ],
)
def test_exit_code(name, code):
    assert run(name) == code


# --- stdout ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["continuous", "split_crossed", "edge_overhang"])
def test_stdout_is_the_text_report(name, capsys):
    run(name)
    board = Board.load(board_path(name))
    expected = to_text(
        check(board, ["SIG"], "In1.Cu", k=3.0),
        board_name=f"{name}.kicad_pcb",  # file name only, not the full path
        ref_layer="In1.Cu",
        nets=["SIG"],
        k=3.0,
    )
    out, err = capsys.readouterr()
    assert out == expected
    assert err == ""


def test_k_reaches_the_check(capsys):
    run("split_crossed", "--k", "1.5")
    assert "k: 1.5" in capsys.readouterr().out


# --- files -----------------------------------------------------------------


def test_json_file_matches_the_library(tmp_path, capsys):
    out_file = tmp_path / "report.json"
    run("split_crossed", "--json", str(out_file))

    board = Board.load(board_path("split_crossed"))
    expected = to_json(
        check(board, ["SIG"], "In1.Cu", k=3.0),
        board_name="split_crossed.kicad_pcb",
        ref_layer="In1.Cu",
        nets=["SIG"],
        k=3.0,
    )
    assert out_file.read_text(encoding="utf-8") == expected
    assert json.loads(out_file.read_text(encoding="utf-8"))["failed"] is True
    # The JSON is extra output, not a replacement for the text report.
    assert "ERROR split" in capsys.readouterr().out


def test_png_file_is_written(tmp_path):
    out_file = tmp_path / "plot.png"
    run("edge_overhang", "--png", str(out_file))
    assert out_file.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_files_are_written_even_when_the_check_fails(tmp_path):
    # The failing run is exactly when CI needs the picture.
    js, png = tmp_path / "r.json", tmp_path / "r.png"
    assert run("split_crossed", "--json", str(js), "--png", str(png)) == 1
    assert js.exists() and png.exists()


def test_no_files_without_the_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run("split_crossed")
    assert list(tmp_path.iterdir()) == []


def test_png_is_not_cropped(tmp_path):
    # Without bbox_inches="tight" the legend (outside the axes) and the
    # y label get cut at the image border. Tight saving leaves a blank margin
    # on every side, so all four edges must be background.
    out_file = tmp_path / "plot.png"
    run("split_crossed", "--png", str(out_file))
    img = mpimg.imread(out_file)[..., :3]
    for edge in (img[0], img[-1], img[:, 0], img[:, -1]):
        assert np.all(edge > 0.98)


def test_no_pyplot_figures_left_open(tmp_path):
    before = plt.get_fignums()
    run("split_crossed", "--png", str(tmp_path / "plot.png"))
    assert plt.get_fignums() == before


# --- could not run: exit 2, a message, no traceback ------------------------


def assert_clean_error(capsys, *words):
    out, err = capsys.readouterr()
    assert out == ""
    assert "Traceback" not in err
    for word in words:
        assert word in err


def test_missing_board_file(tmp_path, capsys):
    missing = tmp_path / "nope.kicad_pcb"
    assert main([str(missing), "--nets", "SIG", "--ref", "In1.Cu"]) == 2
    assert_clean_error(capsys, "nope.kicad_pcb")


def test_board_that_does_not_parse(tmp_path, capsys):
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text("(kicad_pcb (version 2026", encoding="utf-8")
    assert main([str(bad), "--nets", "SIG", "--ref", "In1.Cu"]) == 2
    assert_clean_error(capsys, "bad.kicad_pcb")


def test_ref_layer_with_no_copper(capsys):
    assert main([board_path("split_crossed"), "--nets", "SIG", "--ref", "In2.Cu"]) == 2
    assert_clean_error(capsys, "In2.Cu")


def test_unknown_net_is_an_error_not_a_pass(capsys):
    # A typo must not turn CI green: "SGI" has no tracks, so nothing would be
    # checked and the run would report "No violations".
    assert main([board_path("split_crossed"), "--nets", "SGI", "--ref", "In1.Cu"]) == 2
    assert_clean_error(capsys, "SGI")


def test_one_unknown_net_among_good_ones(capsys):
    assert main([board_path("split_crossed"), "--nets", "SIG,SGI", "--ref", "In1.Cu"]) == 2
    assert_clean_error(capsys, "SGI")


def test_unwritable_output_path(tmp_path, capsys):
    target = tmp_path / "no_such_dir" / "r.json"
    assert run("split_crossed", "--json", str(target)) == 2
    _, err = capsys.readouterr()
    assert "Traceback" not in err
    assert "r.json" in err


# --- the real process, as CI will run it -----------------------------------


@pytest.mark.parametrize(("name", "code"), [("continuous", 0), ("split_crossed", 1)])
def test_python_dash_m(name, code):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "kicad_tog", board_path(name), "--nets", "SIG", "--ref", "In1.Cu"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert result.returncode == code
    assert result.stdout.startswith("Board: ")


# --- --layer: check one signal layer -----------------------------------------


def test_layer_defaults_to_every_layer():
    assert build_parser().parse_args(["b", "--nets", "SIG", "--ref", "In1.Cu"]).layer is None


def test_layer_reaches_the_check_and_the_header(capsys):
    assert run("split_crossed", "--layer", "F.Cu") == 1
    out = capsys.readouterr().out
    assert "Signal layer: F.Cu" in out
    assert "ERROR split net=SIG layer=F.Cu 1.80 mm" in out


def test_layer_filters_out_other_layers(capsys):
    # edge_overhang: SIG on F.Cu, GND on B.Cu. Only F.Cu is checked.
    main([board_path("edge_overhang"), "--nets", "SIG,GND", "--ref", "In1.Cu", "--layer", "F.Cu"])
    out = capsys.readouterr().out
    assert "layer=B.Cu" not in out
    assert "layer=F.Cu" in out


def test_layer_with_no_segments_is_an_error_not_a_pass(capsys):
    # Like a misspelled net: nothing would be checked, and CI would go green.
    assert run("split_crossed", "--layer", "B.Cu") == 2
    assert_clean_error(capsys, "B.Cu", "no segments")


def test_layer_in_the_json(tmp_path, capsys):
    out_file = tmp_path / "r.json"
    run("split_crossed", "--layer", "F.Cu", "--json", str(out_file))
    assert json.loads(out_file.read_text(encoding="utf-8"))["signal_layer"] == "F.Cu"


# --- a KiCad 9 board: nets are numbers + a top-level table ---------------------

KICAD9_BOARD = """(kicad_pcb (version 20241229) (generator "pcbnew")
  (setup (stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.1))
    (layer "In1.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "GND") (net 2 "/USB_D_P")
  (segment (start 2 0) (end 18 0) (width 0.2) (layer "F.Cu") (net 2))
  (zone (net 1) (net_name "GND") (layer "In1.Cu")
    (filled_polygon (layer "In1.Cu") (pts (xy 0 -5) (xy 9 -5) (xy 9 5) (xy 0 5))))
  (zone (net 1) (net_name "GND") (layer "In1.Cu")
    (filled_polygon (layer "In1.Cu") (pts (xy 11 -5) (xy 20 -5) (xy 20 5) (xy 11 5)))))
"""


def test_kicad9_board_is_checked_by_net_name(tmp_path, capsys):
    board = tmp_path / "kicad9.kicad_pcb"
    board.write_text(KICAD9_BOARD, encoding="utf-8")

    code = main([str(board), "--nets", "/USB_D_P", "--ref", "In1.Cu", "--layer", "F.Cu"])

    assert code == 1
    assert "ERROR split net=/USB_D_P layer=F.Cu" in capsys.readouterr().out


# --- automatic references and net selection ----------------------------------


def test_ref_and_nets_are_optional():
    args = build_parser().parse_args(["b.kicad_pcb"])
    assert args.ref is None and args.nets is None


@pytest.mark.parametrize(("name", "code"), [("continuous", 0), ("split_crossed", 1)])
def test_auto_reference_on_the_fixtures(name, code, capsys):
    assert main([board_path(name), "--nets", "SIG"]) == code
    out = capsys.readouterr().out
    assert "Reference layer: auto (In1.Cu)" in out
    assert "ref=In1.Cu" in out


def test_half_plane_is_not_a_plane(capsys):
    # edge_overhang's In1 pour covers 44 % of the board: no plane, so the trace
    # has no reference at all (with --ref In1.Cu it is an overhang warning).
    assert main([board_path("edge_overhang"), "--nets", "SIG"]) == 1
    assert "ERROR no_reference net=SIG" in capsys.readouterr().out


def test_fixtures_have_no_net_classes_so_no_flags_exits_2(capsys):
    assert main([board_path("split_crossed")]) == 2
    assert_clean_error(capsys, "no nets selected")


def test_auto_png_is_one_file_per_plane(tmp_path):
    out = tmp_path / "out.png"
    main([board_path("split_crossed"), "--nets", "SIG", "--png", str(out)])

    assert [p.name for p in tmp_path.iterdir()] == ["out_In1.Cu.png"]


def test_auto_json_names_the_planes_and_the_reference_per_violation(tmp_path, capsys):
    out = tmp_path / "r.json"
    main([board_path("split_crossed"), "--nets", "SIG", "--json", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["ref_layer"] is None
    assert data["reference_planes"] == ["In1.Cu"]
    assert {v["ref_layer"] for v in data["violations"]} == {"In1.Cu"}


def test_net_classes_from_the_project_file(tmp_path, capsys):
    # A copy of split_crossed whose project puts SIG in a 50ohm class: no flags needed.
    board = tmp_path / "board.kicad_pcb"
    board.write_text(Path(board_path("split_crossed")).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "board.kicad_pro").write_text(json.dumps({"net_settings": {
        "netclass_patterns": [{"pattern": "SIG", "netclass": "50ohm"}],
    }}), encoding="utf-8")

    assert main([str(board)]) == 1
    assert "Nets: 1 (net classes: 50ohm: 1)" in capsys.readouterr().out


def test_wildcard_nets_on_the_command_line(capsys):
    assert main([board_path("split_crossed"), "--nets", "S*", "--ref", "In1.Cu"]) == 1
    assert "net=SIG" in capsys.readouterr().out
