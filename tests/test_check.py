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
    Violation,
    check,
    classify,
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
    assert overhang.start == pytest.approx((125.6, 88.0), abs=0.01)
    assert overhang.end == pytest.approx((139.185, 88.0), abs=0.01)
    assert overhang.length == pytest.approx(13.585, abs=0.01)


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
