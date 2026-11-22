"""Tests for report.py (P5): violations -> text, JSON, figure.

report.py only presents. It never parses or does geometry; everything it
needs is in the Violation list and the Board.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from matplotlib import pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from shapely.geometry import MultiPolygon, box

from kicad_tog.board import Board, Segment
from kicad_tog.check import Violation, check
from kicad_tog.report import (
    COLORS,
    failed,
    ordered,
    plot,
    summarize,
    summary_line,
    to_dict,
    to_json,
    to_text,
)
from kicad_tog.s_expression import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def boards():
    return {
        name: Board.load(FIXTURES / f"{name}.kicad_pcb")
        for name in ("continuous", "split_crossed", "edge_overhang")
    }


@pytest.fixture(scope="module")
def results(boards):
    return {name: check(board, ["SIG"], "In1.Cu") for name, board in boards.items()}


def v(kind, severity, net="SIG", start=(0.0, 0.0), end=(1.0, 0.0), length=1.0):
    """Hand-made violation; only the fields a test cares about vary."""
    return Violation(net, "F.Cu", kind, severity, start, end, length)


SPLIT = v("split", "error", start=(5.0, 0.0), end=(6.8, 0.0), length=1.8)
OVERHANG = v("edge_overhang", "warning", start=(8.0, 0.0), end=(12.0, 0.0), length=4.0)
PAD = v("pad_clearance", "info", start=(0.0, 0.0), end=(1.75, 0.0), length=1.75)


# ---- ordered / summarize / failed / summary_line ----

def test_ordered_puts_errors_then_warnings_then_info():
    assert ordered([PAD, OVERHANG, SPLIT]) == [SPLIT, OVERHANG, PAD]


def test_ordered_breaks_ties_by_net_then_position():
    b_far = v("split", "error", net="B", start=(9.0, 0.0))
    a = v("split", "error", net="A", start=(5.0, 0.0))
    b_near = v("split", "error", net="B", start=(1.0, 0.0))

    assert ordered([b_far, a, b_near]) == [a, b_near, b_far]


def test_ordered_does_not_mutate_its_input():
    items = [PAD, SPLIT]
    ordered(items)

    assert items == [PAD, SPLIT]


def test_summarize_always_has_all_three_severities():
    assert summarize([]) == {"error": 0, "warning": 0, "info": 0}
    assert summarize([SPLIT, PAD, PAD]) == {"error": 1, "warning": 0, "info": 2}


@pytest.mark.parametrize(
    ("violations", "expected"),
    [
        ([], False),
        ([PAD, PAD], False),          # option C: info never fails
        ([OVERHANG, PAD], False),     # a warning is reported, not failed
        ([PAD, SPLIT], True),
    ],
)
def test_failed_only_on_a_split(violations, expected):
    assert failed(violations) is expected


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"error": 0, "warning": 0, "info": 0}, "0 errors, 0 warnings, 0 info"),
        ({"error": 1, "warning": 1, "info": 1}, "1 error, 1 warning, 1 info"),
        ({"error": 2, "warning": 3, "info": 2}, "2 errors, 3 warnings, 2 info"),
    ],
)
def test_summary_line_plurals(counts, expected):
    assert summary_line(counts) == expected


# ---- to_text ----

def text_for(results, name):
    return to_text(
        results[name],
        board_name=f"{name}.kicad_pcb",
        ref_layer="In1.Cu",
        nets=["SIG"],
        k=3.0,
    )


def violation_lines(text):
    return [
        line for line in text.splitlines()
        if line.startswith(("ERROR", "WARNING", "INFO"))
    ]


def test_text_header_names_the_run(results):
    first = text_for(results, "split_crossed").splitlines()[0]

    for word in ("split_crossed.kicad_pcb", "In1.Cu", "SIG"):
        assert word in first


def test_text_has_one_line_per_violation_errors_first(results):
    lines = violation_lines(text_for(results, "split_crossed"))

    assert [line.split()[0] for line in lines] == ["ERROR", "INFO", "INFO"]


def test_text_line_shows_kind_net_layer_length_and_position(results):
    (split_line,) = [
        line for line in violation_lines(text_for(results, "split_crossed"))
        if line.startswith("ERROR")
    ]

    for word in ("split", "SIG", "F.Cu", "1.80 mm", "(124.10, 88.00)", "(125.90, 88.00)"):
        assert word in split_line


def test_text_ends_with_the_summary_line(results):
    text = text_for(results, "split_crossed")

    assert text.rstrip("\n").splitlines()[-1] == "1 error, 0 warnings, 2 info"


def test_text_for_a_clean_run(results):
    text = to_text([], board_name="b.kicad_pcb", ref_layer="In1.Cu", nets=["SIG"], k=3.0)

    assert "No violations" in text
    assert violation_lines(text) == []
    assert text.rstrip("\n").splitlines()[-1] == "0 errors, 0 warnings, 0 info"


def test_text_accepts_a_one_shot_iterator(results):
    """A generator can be read only once: read it once, then reuse the list."""
    as_list = text_for(results, "split_crossed")
    as_iter = to_text(
        iter(results["split_crossed"]),
        board_name="split_crossed.kicad_pcb",
        ref_layer="In1.Cu",
        nets=["SIG"],
        k=3.0,
    )

    assert as_iter == as_list


# ---- to_dict / to_json ----

def dict_for(results, name, nets=("SIG",)):
    return to_dict(
        results[name],
        board_name=f"{name}.kicad_pcb",
        ref_layer="In1.Cu",
        nets=nets,
        k=3.0,
    )


def test_dict_header_fields(results):
    data = dict_for(results, "split_crossed")

    assert data["board"] == "split_crossed.kicad_pcb"
    assert data["ref_layer"] == "In1.Cu"
    assert data["nets"] == ["SIG"]
    assert data["k"] == 3.0
    assert data["summary"] == {"error": 1, "warning": 0, "info": 2}
    assert data["failed"] is True


def test_dict_nets_become_a_sorted_list():
    data = to_dict([], board_name="b", ref_layer="In1.Cu", nets={"SIG", "CLK"}, k=3.0)

    assert data["nets"] == ["CLK", "SIG"]


def test_dict_violations_are_ordered_with_all_fields(results):
    items = dict_for(results, "split_crossed")["violations"]

    assert [item["kind"] for item in items] == ["split", "pad_clearance", "pad_clearance"]
    assert set(items[0]) == {
        "net", "layer", "ref_layer", "kind", "severity", "start", "end", "length",
    }


def test_dict_numbers_are_rounded_to_4_decimals(results):
    split = dict_for(results, "split_crossed")["violations"][0]

    assert split["length"] == 1.8          # not 1.8000000000000114
    assert split["start"] == [124.1, 88.0]
    assert split["end"] == [125.9, 88.0]


def test_json_round_trips_to_the_dict(results):
    """Tuples come back from JSON as lists, so the dict must use lists already."""
    text = to_json(
        results["split_crossed"],
        board_name="split_crossed.kicad_pcb",
        ref_layer="In1.Cu",
        nets={"SIG"},
        k=3.0,
    )

    assert json.loads(text) == dict_for(results, "split_crossed")


def test_json_is_pretty_and_ends_with_a_newline(results):
    text = to_json([], board_name="b", ref_layer="In1.Cu", nets=["SIG"], k=3.0)

    assert text.endswith("\n")
    assert len(text.splitlines()) > 1


def test_dict_for_a_clean_run():
    data = to_dict([], board_name="b", ref_layer="In1.Cu", nets=["SIG"], k=3.0)

    assert data["violations"] == []
    assert data["failed"] is False


# ---- plot ----

def lines_with(ax, gid):
    return [line for line in ax.lines if line.get_gid() == gid]


def fig_for(boards, results, name):
    return plot(boards[name], results[name], nets=["SIG"], ref_layer="In1.Cu")


def render(fig):
    """Draw the figure off-screen; return its RGBA pixels."""
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    return np.asarray(canvas.buffer_rgba())


def pixel_at(fig, pixels, xy):
    """RGBA of the pixel under board point xy (mm)."""
    ax = fig.axes[0]
    x, y = ax.transData.transform(xy)
    return tuple(int(c) for c in pixels[int(pixels.shape[0] - y), int(x)])


def background(fig):
    return tuple(round(c * 255) for c in to_rgba(fig.axes[0].get_facecolor()))


def test_plot_returns_a_figure_without_touching_pyplot(boards, results):
    """Library code must not open windows or pile up pyplot figures."""
    before = plt.get_fignums()
    fig = fig_for(boards, results, "split_crossed")

    assert isinstance(fig, Figure)
    assert len(fig.axes) == 1
    assert plt.get_fignums() == before


def test_plot_is_to_scale_and_not_mirrored(boards, results):
    ax = fig_for(boards, results, "split_crossed").axes[0]

    assert ax.get_aspect() == 1.0      # 1 mm in x == 1 mm in y
    assert ax.yaxis_inverted()         # KiCad's y points down


def test_plot_draws_one_line_per_span_in_its_colour(boards, results):
    ax = fig_for(boards, results, "split_crossed").axes[0]
    (split,) = lines_with(ax, "split")

    assert split.get_xdata() == pytest.approx([124.1, 125.9], abs=0.01)
    assert split.get_ydata() == pytest.approx([88.0, 88.0], abs=0.01)
    assert len(lines_with(ax, "pad_clearance")) == 2
    assert lines_with(ax, "edge_overhang") == []
    for kind in ("split", "pad_clearance"):
        for line in lines_with(ax, kind):
            assert to_rgba(line.get_color()) == to_rgba(COLORS[kind])


def test_colours_tell_the_kinds_apart():
    assert set(COLORS) == {"split", "edge_overhang", "pad_clearance", "no_reference"}
    assert len({to_rgba(c) for c in COLORS.values()}) == 4


def test_spans_are_drawn_on_top_of_the_trace(boards, results):
    ax = fig_for(boards, results, "split_crossed").axes[0]
    (trace,) = lines_with(ax, "trace")
    (split,) = lines_with(ax, "split")

    assert split.get_zorder() > trace.get_zorder()


def test_plot_draws_only_the_checked_nets(boards, results):
    """edge_overhang also has a GND segment on B.Cu (y = 90.54): not drawn."""
    ax = fig_for(boards, results, "edge_overhang").axes[0]
    traces = lines_with(ax, "trace")

    assert len(traces) == 1
    assert traces[0].get_ydata() == pytest.approx([88.0, 88.0])


def test_plot_marks_both_trace_ends(boards, results):
    ax = fig_for(boards, results, "split_crossed").axes[0]
    points = {
        (round(x, 2), round(y, 2))
        for line in lines_with(ax, "endpoint")
        for x, y in zip(line.get_xdata(), line.get_ydata())
    }

    assert {(111.91, 88.0), (138.09, 88.0)} <= points


def test_plot_labels_errors_and_warnings_but_not_info(boards, results):
    ax = fig_for(boards, results, "split_crossed").axes[0]
    labels = [text.get_text() for text in ax.texts]

    assert len(labels) == 1
    assert "1.80 mm" in labels[0]


def test_legend_lists_each_kind_once(boards, results):
    """One label= per span would list pad_clearance twice."""
    legend = fig_for(boards, results, "split_crossed").axes[0].get_legend()
    entries = [text.get_text() for text in legend.get_texts()]

    assert sorted(entries) == ["pad_clearance", "split"]


def test_legend_never_covers_the_board(boards, results):
    """loc="best" happily parks the legend on empty board area."""
    fig = fig_for(boards, results, "edge_overhang")
    render(fig)
    legend = fig.axes[0].get_legend()

    assert not legend.get_window_extent().overlaps(fig.axes[0].bbox)


@pytest.mark.parametrize(
    ("name", "empty_point"),
    [
        ("split_crossed", (125.0, 92.0)),   # inside the slot (x 124.5-125.5, y 85-93)
        ("edge_overhang", (133.0, 92.0)),   # past the plane's edge at x = 129
    ],
)
def test_missing_copper_stays_empty(boards, results, name, empty_point):
    """Filling a polygon's outline only would paint over the slot it should show."""
    fig = fig_for(boards, results, name)
    pixels = render(fig)

    assert pixel_at(fig, pixels, empty_point) == background(fig)
    assert pixel_at(fig, pixels, (120.0, 92.0)) != background(fig)   # plane


@pytest.mark.parametrize(
    ("name", "kind", "mid"),
    [
        ("split_crossed", "split", (125.0, 88.0)),
        ("edge_overhang", "edge_overhang", (132.39, 88.0)),
    ],
)
def test_span_colour_is_visible_at_its_midpoint(boards, results, name, kind, mid):
    """Nothing (label box, marker) may sit on top of the span it explains."""
    fig = fig_for(boards, results, name)
    pixels = render(fig)
    expected = tuple(round(c * 255) for c in to_rgba(COLORS[kind]))

    got = pixel_at(fig, pixels, mid)
    assert max(abs(a - b) for a, b in zip(got, expected)) <= 10


STACKUP_2L = """(stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.1))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 2" (type "core") (thickness 1.4))
    (layer "B.Cu" (type "copper") (thickness 0.035)))"""


def test_plot_fills_every_net_on_the_reference_layer_only():
    """Same rule as check(): all pours on ref_layer count, other layers don't."""
    board = Board(
        segments=[Segment((2.0, 0.0), (18.0, 0.0), 0.2, "F.Cu", "SIG")],
        fills={
            ("GND", "In1.Cu"): MultiPolygon([box(0, -5, 9, 5)]),
            ("VCC", "In1.Cu"): MultiPolygon([box(11, -5, 20, 5)]),
            ("GND", "B.Cu"): MultiPolygon([box(9, -5, 11, 5)]),   # under the gap
        },
        stackup=parse(STACKUP_2L),
    )
    fig = plot(board, [], nets=["SIG"], ref_layer="In1.Cu")
    pixels = render(fig)

    assert pixel_at(fig, pixels, (15.0, 3.0)) != background(fig)   # VCC pour
    assert pixel_at(fig, pixels, (10.0, 3.0)) == background(fig)   # B.Cu only


def test_plot_for_a_clean_board(boards):
    fig = plot(boards["continuous"], [], nets=["SIG"], ref_layer="In1.Cu")
    ax = fig.axes[0]

    assert len(lines_with(ax, "trace")) == 1
    assert len(ax.texts) == 0
    for kind in COLORS:
        assert lines_with(ax, kind) == []


def test_plot_saves_a_png(boards, results, tmp_path):
    path = tmp_path / "split_crossed.png"
    fig_for(boards, results, "split_crossed").savefig(path, dpi=100)

    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# ---- signal_layer: shown in the header and JSON, filters the plotted traces ----

def test_text_header_names_the_signal_layer_when_given():
    text = to_text([], board_name="b", ref_layer="In1.Cu", nets=["SIG"], k=3.0, signal_layer="F.Cu")

    assert text.splitlines()[0].endswith("| Signal layer: F.Cu")


def test_text_header_has_no_signal_layer_by_default():
    text = to_text([], board_name="b", ref_layer="In1.Cu", nets=["SIG"], k=3.0)

    assert "Signal layer" not in text


def test_dict_signal_layer_is_null_by_default_and_set_when_given():
    default = to_dict([], board_name="b", ref_layer="In1.Cu", nets=["SIG"], k=3.0)
    given = to_dict([], board_name="b", ref_layer="In1.Cu", nets=["SIG"], k=3.0, signal_layer="F.Cu")

    assert default["signal_layer"] is None
    assert given["signal_layer"] == "F.Cu"
    assert json.loads(
        to_json([], board_name="b", ref_layer="In1.Cu", nets=["SIG"], k=3.0, signal_layer="F.Cu")
    ) == given


def test_plot_draws_only_traces_on_the_signal_layer():
    board = Board(
        segments=[
            Segment((2.0, 0.0), (18.0, 0.0), 0.2, "F.Cu", "SIG"),
            Segment((2.0, 3.0), (18.0, 3.0), 0.2, "B.Cu", "SIG"),
        ],
        fills={("GND", "In1.Cu"): MultiPolygon([box(0, -5, 20, 5)])},
        stackup=parse(STACKUP_2L),
    )
    every = plot(board, [], nets=["SIG"], ref_layer="In1.Cu").axes[0]
    front = plot(board, [], nets=["SIG"], ref_layer="In1.Cu", signal_layer="F.Cu").axes[0]

    assert len(lines_with(every, "trace")) == 2
    (trace,) = lines_with(front, "trace")
    assert trace.get_ydata() == pytest.approx([0.0, 0.0])


# ---- automatic references: header, ref= per line, JSON fields ----

AUTO_SPLIT = Violation("SIG", "F.Cu", "split", "error", (5.0, 0.0), (6.8, 0.0), 1.8, "In1.Cu")
NO_REF = Violation("SIG", "B.Cu", "no_reference", "error", (0.0, 3.0), (16.0, 3.0), 16.0, None)
AUTO = {"board_name": "b", "ref_layer": None, "nets": ["SIG"], "k": 3.0, "planes": ["In1.Cu"]}


def test_auto_header_names_the_planes():
    text = to_text([], **AUTO)

    assert text.splitlines()[0].startswith("Board: b | Reference layer: auto (In1.Cu) |")


def test_auto_lines_name_their_reference():
    lines = violation_lines(to_text([AUTO_SPLIT, NO_REF], **AUTO))

    assert lines == [
        "ERROR no_reference net=SIG layer=B.Cu ref=none 16.00 mm (0.00, 3.00) -> (16.00, 3.00)",
        "ERROR split net=SIG layer=F.Cu ref=In1.Cu 1.80 mm (5.00, 0.00) -> (6.80, 0.00)",
    ]


def test_given_reference_lines_stay_as_before(results):
    assert "ref=" not in text_for(results, "split_crossed")


def test_chosen_nets_show_a_count_and_the_reason():
    text = to_text([], **{**AUTO, "nets": ["B", "A"], "nets_reason": "net classes: 90ohm: 2"})

    assert "| Nets: 2 (net classes: 90ohm: 2) |" in text


def test_auto_dict_fields():
    data = to_dict([AUTO_SPLIT], **{**AUTO, "nets_reason": "pair names, no net classes"})

    assert data["ref_layer"] is None
    assert data["reference_planes"] == ["In1.Cu"]
    assert data["net_selection"] == "pair names, no net classes"
    assert data["violations"][0]["ref_layer"] == "In1.Cu"


def test_given_reference_dict_fields(results):
    data = dict_for(results, "split_crossed")

    assert data["reference_planes"] == ["In1.Cu"]
    assert data["net_selection"] is None


def test_plot_trace_layers_keeps_only_those_layers():
    board = Board(
        segments=[
            Segment((2.0, 0.0), (18.0, 0.0), 0.2, "F.Cu", "SIG"),
            Segment((2.0, 3.0), (18.0, 3.0), 0.2, "B.Cu", "SIG"),
        ],
        fills={("GND", "In1.Cu"): MultiPolygon([box(0, -5, 20, 5)])},
        stackup=parse(STACKUP_2L),
    )
    ax = plot(board, [], nets=["SIG"], ref_layer="In1.Cu", trace_layers=["B.Cu"]).axes[0]
    (trace,) = lines_with(ax, "trace")

    assert trace.get_ydata() == pytest.approx([3.0, 3.0])
