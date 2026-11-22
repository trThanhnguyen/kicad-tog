"""Tests for check.py:
board -> uncovered spans along each trace (P3 core) -> classify + floor (P3b).
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, box
from shapely.ops import unary_union

from kicad_tog.board import Board, Segment
from kicad_tog.check import (
    MIN_SPAN,
    PAD_CLEARANCE_MAX,
    LocalCopper,
    Reference,
    Violation,
    check,
    classify,
    plane_layers,
    reference_map,
    references,
    uncovered_spans,
)
from kicad_tog.s_expression import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def boards():
    return {
        name: Board.load(FIXTURES / f"{name}.kicad_pcb")
        for name in ("continuous", "split_crossed", "edge_overhang")
    }


def xs(span):
    """(start x, end x) of a horizontal span, rounded for comparison."""
    (x0, _), (x1, _) = span.coords[0], span.coords[-1]
    return round(x0, 2), round(x1, 2)


# ---- uncovered_spans: hand-made geometry, trace (2,0) -> (18,0), r = 0.5 ----

TRACE = LineString([(2, 0), (18, 0)])


def test_trace_well_inside_copper_has_no_span():
    copper = box(0, -5, 20, 5)

    assert uncovered_spans(TRACE, copper, 0.5) == []


def test_slot_across_the_trace_gives_one_span():
    copper = unary_union([box(0, -5, 9, 5), box(11, -5, 20, 5)])  # gap x 9..11
    spans = uncovered_spans(TRACE, copper, 0.5)

    assert len(spans) == 1
    assert xs(spans[0]) == (8.5, 11.5)   # gap widened by r on each side
    assert spans[0].length == pytest.approx(3.0)


def test_copper_ending_under_the_trace_gives_a_span_to_the_end():
    copper = box(0, -5, 10, 5)
    spans = uncovered_spans(TRACE, copper, 0.5)

    assert len(spans) == 1
    assert xs(spans[0]) == (9.5, 18.0)


def test_strip_narrower_than_2r_counts_as_uncovered():
    # copper is directly under the centerline, but the disc of radius r leaves it
    copper = box(0, -0.4, 20, 0.4)
    spans = uncovered_spans(TRACE, copper, 0.5)

    assert len(spans) == 1
    assert spans[0].length == pytest.approx(16.0)


def test_hole_beside_the_trace_counts_within_r():
    # hole 0.3 from the centerline: the disc reaches it from x = 8.6 to 11.4
    copper = box(0, -5, 20, 5).difference(box(9, 0.3, 11, 2))
    spans = uncovered_spans(TRACE, copper, 0.5)

    assert len(spans) == 1
    assert spans[0].length == pytest.approx(2.8, abs=0.01)


def test_hole_beside_the_trace_beyond_r_is_ignored():
    copper = box(0, -5, 20, 5).difference(box(9, 0.6, 11, 2))

    assert uncovered_spans(TRACE, copper, 0.5) == []


def test_spans_are_ordered_and_oriented_along_the_trace():
    # trace drawn right-to-left: spans come out right-to-left too
    backwards = LineString([(18, 0), (2, 0)])
    copper = unary_union([box(0, -5, 5, 5), box(7, -5, 13, 5), box(15, -5, 20, 5)])
    spans = uncovered_spans(backwards, copper, 0.5)

    assert [xs(s) for s in spans] == [(15.5, 12.5), (7.5, 4.5)]


# ---- classify: one span -> (kind, severity), or None below the floor ----

def span(x0, x1):
    return LineString([(x0, 0), (x1, 0)])


def test_sliver_is_dropped():
    assert classify(span(10, 10 + MIN_SPAN / 2), TRACE) is None


def test_span_touching_neither_end_is_a_split():
    assert classify(span(9, 11), TRACE) == ("split", "error")


def test_short_split_is_still_an_error():
    # the floor is for numerical noise, not for short gaps
    assert classify(span(10, 10.1), TRACE) == ("split", "error")


def test_short_span_at_a_trace_end_is_pad_clearance():
    assert classify(span(2, 2 + PAD_CLEARANCE_MAX - 0.1), TRACE) == ("pad_clearance", "info")
    assert classify(span(18 - PAD_CLEARANCE_MAX + 0.1, 18), TRACE) == ("pad_clearance", "info")


def test_long_span_at_a_trace_end_is_edge_overhang():
    assert classify(span(18 - PAD_CLEARANCE_MAX - 0.1, 18), TRACE) == ("edge_overhang", "warning")


# ---- check: the three fixtures (SIG on F.Cu over GND on In1.Cu, r = 0.4) ----

def kinds(violations):
    return sorted(v.kind for v in violations)


def test_continuous_has_only_pad_clearance(boards):
    result = check(boards["continuous"], ["SIG"], "In1.Cu")

    assert kinds(result) == ["pad_clearance", "pad_clearance"]
    assert all(v.severity == "info" for v in result)


def test_split_crossed_finds_the_slot(boards):
    result = check(boards["split_crossed"], ["SIG"], "In1.Cu")
    (split,) = [v for v in result if v.kind == "split"]

    assert kinds(result) == ["pad_clearance", "pad_clearance", "split"]
    assert (split.net, split.layer, split.severity) == ("SIG", "F.Cu", "error")
    assert split.start == pytest.approx((124.1, 88.0), abs=0.01)
    assert split.end == pytest.approx((125.9, 88.0), abs=0.01)
    assert split.length == pytest.approx(1.8, abs=0.01)


def test_edge_overhang_finds_the_plane_edge(boards):
    result = check(boards["edge_overhang"], ["SIG"], "In1.Cu")
    (overhang,) = [v for v in result if v.kind == "edge_overhang"]

    assert kinds(result) == ["edge_overhang", "pad_clearance"]
    assert overhang.severity == "warning"
    assert overhang.start == pytest.approx((128.6, 88.0), abs=0.01)
    assert overhang.end == pytest.approx((139.185, 88.0), abs=0.01)
    assert overhang.length == pytest.approx(10.585, abs=0.01)


def test_k_sets_the_margin(boards):
    # k = 0: r = w/2 = 0.1, so the slot span is the 1 mm slot + 0.1 each side
    result = check(boards["split_crossed"], ["SIG"], "In1.Cu", k=0)
    (split,) = [v for v in result if v.kind == "split"]

    assert split.length == pytest.approx(1.2, abs=0.01)


def test_only_the_chosen_nets_are_checked(boards):
    assert check(boards["split_crossed"], ["NOT_A_NET"], "In1.Cu") == []


def test_reference_layer_without_copper_raises(boards):
    with pytest.raises(ValueError, match="B.Cu"):
        check(boards["split_crossed"], ["SIG"], "B.Cu")


def test_violation_is_frozen(boards):
    v = check(boards["split_crossed"], ["SIG"], "In1.Cu")[0]

    with pytest.raises(FrozenInstanceError):
        v.length = 0.0


# ---- reference copper = every fill on ref_layer, whatever its net ----

STACKUP_2L = """(stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.1))
    (layer "In1.Cu" (type "copper") (thickness 0.035)))"""

SIG = Segment((2.0, 0.0), (18.0, 0.0), 0.2, "F.Cu", "SIG")  # r = 0.1 + 3 * 0.1 = 0.4


def board_with(fills):
    return Board(segments=[SIG], fills=fills, stackup=parse(STACKUP_2L))


def test_power_plane_is_a_valid_reference():
    board = board_with({("3V3", "In1.Cu"): MultiPolygon([box(0, -5, 20, 5)])})

    assert check(board, ["SIG"], "In1.Cu") == []


def test_gap_between_two_pours_is_a_split():
    # GND left, 3V3 right, 0.5 mm apart: only the gap (+ r each side) is uncovered
    board = board_with({
        ("GND", "In1.Cu"): MultiPolygon([box(0, -5, 9.75, 5)]),
        ("3V3", "In1.Cu"): MultiPolygon([box(10.25, -5, 20, 5)]),
    })
    (split,) = check(board, ["SIG"], "In1.Cu")

    assert split.kind == "split"
    assert split.start == pytest.approx((9.35, 0.0), abs=0.01)
    assert split.end == pytest.approx((10.65, 0.0), abs=0.01)


# ---- signal_layer: only segments on one layer (real boards change layers) ----

STACKUP_3L = """(stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.1))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 2" (type "core") (thickness 0.2))
    (layer "In2.Cu" (type "copper") (thickness 0.035)))"""

# The same net crosses the 9..11 slot twice: on F.Cu and, after a via, on In2.Cu.
TWO_LAYER_NET = Board(
    segments=[
        Segment((2.0, 0.0), (18.0, 0.0), 0.2, "F.Cu", "SIG"),
        Segment((2.0, 3.0), (18.0, 3.0), 0.2, "In2.Cu", "SIG"),
    ],
    fills={("GND", "In1.Cu"): MultiPolygon([box(0, -5, 9, 8), box(11, -5, 20, 8)])},
    stackup=parse(STACKUP_3L),
)


def test_without_signal_layer_every_layer_is_checked():
    layers = {v.layer for v in check(TWO_LAYER_NET, ["SIG"], "In1.Cu")}

    assert layers == {"F.Cu", "In2.Cu"}


def test_signal_layer_keeps_only_that_layer():
    result = check(TWO_LAYER_NET, ["SIG"], "In1.Cu", signal_layer="F.Cu")

    assert [(v.layer, v.kind) for v in result] == [("F.Cu", "split")]


def test_signal_layer_with_no_segments_finds_nothing():
    assert check(TWO_LAYER_NET, ["SIG"], "In1.Cu", signal_layer="B.Cu") == []


def test_signal_layer_equal_to_ref_layer_raises():
    with pytest.raises(ValueError, match="In1.Cu"):
        check(TWO_LAYER_NET, ["SIG"], "In1.Cu", signal_layer="In1.Cu")


# ---- LocalCopper: per-trace clipping must not change any answer ----

def perforated_plane():
    """A 40 x 40 plane with a slot, an edge notch and a grid of via-sized holes."""
    plane = box(0, 0, 40, 40)
    holes = [Point(x + 0.5, y + 0.5).buffer(0.3) for x in range(2, 38, 3) for y in range(2, 38, 3)]
    cuts = unary_union([*holes, box(19.5, 5, 20.5, 35), box(0, 18, 4, 22)])
    return plane.difference(cuts)


# Straight, diagonal, short, long, and one running off the plane edge.
TRACES = [
    LineString([(1, 10), (39, 10)]),
    LineString([(5, 5), (35, 33)]),
    LineString([(10, 30), (12, 30.4)]),
    LineString([(2, 20), (8, 20)]),
    LineString([(30, 1), (30, 39)]),
    LineString([(25, 25), (45, 25)]),
]


@pytest.mark.parametrize("trace", TRACES, ids=range(len(TRACES)))
@pytest.mark.parametrize("radius", [0.1, 0.4, 1.5])
def test_local_copper_gives_the_same_spans_as_the_whole_plane(trace, radius):
    plane = perforated_plane()
    local = LocalCopper(plane).near(trace, radius)

    full = uncovered_spans(trace, plane, radius)
    clipped = uncovered_spans(trace, local, radius)

    assert len(clipped) == len(full)
    for a, b in zip(clipped, full):
        assert a.length == pytest.approx(b.length, abs=1e-6)
        assert a.coords[0] == pytest.approx(b.coords[0], abs=1e-6)
        assert a.coords[-1] == pytest.approx(b.coords[-1], abs=1e-6)


def test_local_copper_keeps_only_nearby_pours():
    far = box(100, 100, 110, 110)
    near = box(0, -5, 20, 5)
    local = LocalCopper(MultiPolygon([near, far])).near(TRACE, 0.5)

    assert local.bounds == pytest.approx((1.0, -1.0, 19.0, 1.0))   # window = trace +- 2r
    assert not local.intersects(far)


def test_local_copper_accepts_a_single_polygon():
    local = LocalCopper(box(0, -5, 20, 5)).near(TRACE, 0.5)

    assert local.area == pytest.approx(18.0 * 2.0)


def test_local_copper_with_nothing_nearby_is_empty():
    local = LocalCopper(box(100, 100, 110, 110)).near(TRACE, 0.5)

    assert local.is_empty
    assert len(uncovered_spans(TRACE, local, 0.5)) == 1   # the whole trace is uncovered


# ---- automatic references: planes by coverage, nearest above / below ----

# Six copper layers; h between neighbours: 0.1, 0.2, 0.3, 0.2, 0.1.
STACKUP_6L = """(stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "d1" (type "prepreg") (thickness 0.1))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "d2" (type "core") (thickness 0.2))
    (layer "In2.Cu" (type "copper") (thickness 0.035))
    (layer "d3" (type "prepreg") (thickness 0.3))
    (layer "In3.Cu" (type "copper") (thickness 0.035))
    (layer "d4" (type "core") (thickness 0.2))
    (layer "In4.Cu" (type "copper") (thickness 0.035))
    (layer "d5" (type "prepreg") (thickness 0.1))
    (layer "B.Cu" (type "copper") (thickness 0.035)))"""

OUTLINE = (0.0, -5.0, 20.0, 5.0)              # 200 mm²
FULL = MultiPolygon([box(0, -5, 20, 5)])      # 100 %
SLOTTED = MultiPolygon([box(0, -5, 9, 5), box(11, -5, 20, 5)])  # 90 %, slot x 9..11
SMALL = MultiPolygon([box(0, -5, 8, 5)])      # 40 %: not a plane


def six_layer(segments, fills):
    return Board(segments=segments, fills=fills, stackup=parse(STACKUP_6L), outline=OUTLINE)


def seg(layer, net="SIG"):
    return Segment((2.0, 0.0), (18.0, 0.0), 0.2, layer, net)


def test_a_plane_covers_at_least_half_the_board():
    board = six_layer([], {
        ("GND", "In1.Cu"): FULL,
        ("GND", "In2.Cu"): SMALL,
        ("3V3", "In3.Cu"): MultiPolygon([box(0, -5, 9, 5)]),   # two nets, 45 % + 45 %:
        ("1V8", "In3.Cu"): MultiPolygon([box(11, -5, 20, 5)]),  # a split power plane
    })

    assert plane_layers(board) == ["In1.Cu", "In3.Cu"]


def test_outer_layer_has_one_reference():
    board = six_layer([], {("GND", "In1.Cu"): FULL, ("GND", "In4.Cu"): FULL})

    assert references(board, "F.Cu", plane_layers(board)) == [
        Reference("In1.Cu", pytest.approx(0.1), nearest=True)
    ]


def test_inner_layer_has_a_nearest_and_a_farther_plane():
    # In2 sits between In1 (h 0.2) and In3 (h 0.3)
    board = six_layer([], {("GND", "In1.Cu"): FULL, ("GND", "In3.Cu"): FULL})
    refs = {r.layer: r for r in references(board, "In2.Cu", plane_layers(board))}

    assert refs["In1.Cu"].nearest and refs["In1.Cu"].h == pytest.approx(0.2)
    assert not refs["In3.Cu"].nearest and refs["In3.Cu"].h == pytest.approx(0.3)


def test_planes_within_10_percent_are_both_nearest():
    # In3 sits between In2 (h 0.3) and In4 (h 0.2): 0.3 > 1.1 * 0.2, not a tie;
    # In3.Cu with planes on In2 (0.3) and B.Cu (0.2 + 0.1 = 0.3): a tie.
    board = six_layer([], {("GND", "In2.Cu"): FULL, ("GND", "B.Cu"): FULL})
    refs = references(board, "In3.Cu", plane_layers(board))

    assert [(r.layer, r.nearest) for r in refs] == [("In2.Cu", True), ("B.Cu", True)]


def test_only_the_nearest_plane_on_each_side_counts():
    board = six_layer([], {
        ("GND", "In1.Cu"): FULL, ("GND", "In2.Cu"): FULL, ("GND", "In4.Cu"): FULL,
    })

    assert [r.layer for r in references(board, "F.Cu", plane_layers(board))] == ["In1.Cu"]


def test_the_traces_own_layer_is_never_its_reference():
    # B.Cu is a full GND flood and also carries the trace
    board = six_layer([], {("GND", "B.Cu"): FULL, ("GND", "In4.Cu"): FULL})

    assert [r.layer for r in references(board, "B.Cu", plane_layers(board))] == ["In4.Cu"]


def test_gap_in_the_nearest_plane_is_an_error():
    board = six_layer([seg("In2.Cu")], {("GND", "In1.Cu"): SLOTTED, ("GND", "In3.Cu"): FULL})
    (split,) = check(board, ["SIG"])

    assert (split.kind, split.severity, split.ref_layer) == ("split", "error", "In1.Cu")
    # r = 0.1 + 3 * 0.2 = 0.7 around the 2 mm slot
    assert split.length == pytest.approx(2.0 + 2 * 0.7, abs=0.01)


def test_gap_in_the_farther_plane_is_a_warning():
    board = six_layer([seg("In2.Cu")], {("GND", "In1.Cu"): FULL, ("GND", "In3.Cu"): SLOTTED})
    (split,) = check(board, ["SIG"])

    assert (split.kind, split.severity, split.ref_layer) == ("split", "warning", "In3.Cu")


def test_gap_in_either_tied_plane_is_an_error():
    board = six_layer([seg("In3.Cu")], {("GND", "In2.Cu"): FULL, ("GND", "B.Cu"): SLOTTED})
    (split,) = check(board, ["SIG"])

    assert (split.severity, split.ref_layer) == ("error", "B.Cu")


def test_trace_with_no_plane_is_one_no_reference_error():
    board = six_layer([seg("F.Cu")], {("GND", "In1.Cu"): SMALL})
    (item,) = check(board, ["SIG"])

    assert (item.kind, item.severity, item.ref_layer) == ("no_reference", "error", None)
    assert item.length == pytest.approx(16.0)


def test_auto_on_split_crossed_matches_the_given_reference(boards):
    """In1.Cu covers ~86 % of the fixture, so auto picks it and gets the same spans."""
    given = check(boards["split_crossed"], ["SIG"], "In1.Cu")
    auto = check(boards["split_crossed"], ["SIG"])

    assert auto == given
    assert {v.ref_layer for v in auto} == {"In1.Cu"}


def test_given_reference_is_recorded_on_each_violation(boards):
    assert {v.ref_layer for v in check(boards["split_crossed"], ["SIG"], "In1.Cu")} == {"In1.Cu"}


def test_auto_without_stackup_raises():
    board = Board(segments=[SIG], fills={("GND", "In1.Cu"): FULL}, stackup=[], outline=OUTLINE)

    with pytest.raises(ValueError, match="stackup"):
        check(board, ["SIG"])


def test_reference_map_lists_planes_and_their_signal_layers():
    board = six_layer(
        [seg("F.Cu"), seg("In2.Cu"), seg("B.Cu", net="OTHER")],
        {("GND", "In1.Cu"): FULL, ("GND", "In3.Cu"): FULL, ("GND", "In4.Cu"): FULL},
    )

    assert reference_map(board, ["SIG"]) == {"In1.Cu": ["F.Cu", "In2.Cu"], "In3.Cu": ["In2.Cu"]}
    assert reference_map(board, ["SIG"], signal_layer="F.Cu") == {"In1.Cu": ["F.Cu"]}
