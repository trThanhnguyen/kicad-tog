"""Tests for board.py:
(load → select nets → trace geometry → fill union → …)
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from shapely.geometry import MultiPolygon, Point

from kicad_tog.board import Board, Segment, dielectric_height
from kicad_tog.s_expression import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def boards():
    return {
        name: Board.load(FIXTURES / f"{name}.kicad_pcb")
        for name in ("continuous", "split_crossed", "edge_overhang")
    }


# ---- Segment: one (segment ...) node -> one frozen object ----

SEGMENT = """(segment (start 113 88) (end 139.185 88) (width 0.2)
    (layer "F.Cu") (net "SIG") (uuid "b4a57a80"))"""


def test_segment_from_s_expression_reads_numbers_as_floats():
    seg = Segment.from_s_expression(parse(SEGMENT))

    assert seg.start == (113.0, 88.0)
    assert seg.end == (139.185, 88.0)
    assert seg.width == 0.2
    assert isinstance(seg.width, float)
    assert seg.layer == "F.Cu"
    assert seg.net == "SIG"


def test_segment_is_frozen():
    seg = Segment.from_s_expression(parse(SEGMENT))

    with pytest.raises(FrozenInstanceError):
        seg.width = 0.3


# ---- dielectric_height: h = sum of dielectric thicknesses between two coppers ----

# KiCad's default 4-layer stackup, trimmed to the parts that matter.
STACKUP_4L = """(stackup
    (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "prepreg") (thickness 0.1) (material "FR4"))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 2" (type "core") (thickness 1.24) (material "FR4"))
    (layer "In2.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 3" (type "prepreg") (thickness 0.1) (material "FR4"))
    (layer "B.Cu" (type "copper") (thickness 0.035))
    (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01)))"""

# 2-ply prepreg from KiCad: one node, a bare `addsublayer` atom, two (thickness ...).
STACKUP_SUBLAYER = """(stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1"
        (type "prepreg")
        (thickness 0.1)
        (material "FR4")
        (epsilon_r 4.5)
        (loss_tangent 0.02) addsublayer
        (color "FR4 natural")
        (thickness 0.1)
        (material "FR4")
        (epsilon_r 4.5)
        (loss_tangent 0.02))
    (layer "In1.Cu" (type "copper") (thickness 0.035)))"""


def test_h_adjacent_layers_is_one_dielectric():
    assert dielectric_height(parse(STACKUP_4L), "F.Cu", "In1.Cu") == pytest.approx(0.1)


def test_h_skips_copper_thickness():
    # 0.1 + 1.24, not 0.1 + 0.035 + 1.24
    assert dielectric_height(parse(STACKUP_4L), "F.Cu", "In2.Cu") == pytest.approx(1.34)


def test_h_does_not_care_about_argument_order():
    stackup = parse(STACKUP_4L)

    assert dielectric_height(stackup, "In1.Cu", "F.Cu") == pytest.approx(
        dielectric_height(stackup, "F.Cu", "In1.Cu")
    )


def test_h_counts_every_sublayer():
    # child(layer, "thickness") would give 0.1: use children()
    assert dielectric_height(parse(STACKUP_SUBLAYER), "F.Cu", "In1.Cu") == pytest.approx(0.2)


def test_h_unknown_layer_raises():
    with pytest.raises(ValueError, match="In3.Cu"):
        dielectric_height(parse(STACKUP_4L), "F.Cu", "In3.Cu")


# ---- Board.from_s_expression: small hand-made boards ----

def test_zone_without_fill_adds_nothing():
    # a rule area (keepout) has no net and no filled_polygon
    tree = parse("""(kicad_pcb
        (zone (layer "In1.Cu") (keepout (copperpour not_allowed))
              (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))))""")

    assert Board.from_s_expression(tree).fills == {}


def test_fill_layer_comes_from_filled_polygon_not_zone():
    # a zone on two layers writes one filled_polygon per layer, each with its own (layer ...)
    tree = parse("""(kicad_pcb
        (zone (net "GND") (layers "In1.Cu" "B.Cu")
            (filled_polygon (layer "In1.Cu") (pts (xy 0 0) (xy 2 0) (xy 2 2) (xy 0 2)))
            (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 1 0) (xy 1 1) (xy 0 1)))))""")
    fills = Board.from_s_expression(tree).fills

    assert fills[("GND", "In1.Cu")].area == pytest.approx(4.0)
    assert fills[("GND", "B.Cu")].area == pytest.approx(1.0)


# ---- Board.load on the three fixtures ----

def test_fixture_segments(boards):
    assert len(boards["continuous"].segments) == 1
    assert len(boards["split_crossed"].segments) == 1
    assert len(boards["edge_overhang"].segments) == 2  # SIG on F.Cu + GND on B.Cu

    sig = boards["continuous"].segments[0]
    assert (sig.net, sig.layer, sig.width) == ("SIG", "F.Cu", 0.2)
    assert sig.start == (113.0, 88.0)
    assert sig.end == (139.185, 88.0)


def test_fixture_fill_is_a_multipolygon(boards):
    for board in boards.values():
        assert isinstance(board.fills[("GND", "In1.Cu")], MultiPolygon)


def test_fixture_fills_only_on_in1(boards):
    for board in boards.values():
        assert set(board.fills) == {("GND", "In1.Cu")}


def test_fixture_h(boards):
    for board in boards.values():
        assert board.h("F.Cu", "In1.Cu") == pytest.approx(0.1)


def test_continuous_has_copper_under_the_middle(boards):
    gnd = boards["continuous"].fills[("GND", "In1.Cu")]

    assert gnd.contains(Point(125, 88))
    assert gnd.contains(Point(130, 88))


def test_split_crossed_slot_is_8_square_mm(boards):
    gnd = boards["split_crossed"].fills[("GND", "In1.Cu")]
    solid = boards["continuous"].fills[("GND", "In1.Cu")]

    assert not gnd.contains(Point(125, 88))  # inside the slot, under SIG
    assert gnd.contains(Point(125, 84))      # past the slot's end: plane is one piece
    assert solid.area - gnd.area == pytest.approx(8.0, abs=0.01)


def test_edge_overhang_plane_stops_at_126(boards):
    gnd = boards["edge_overhang"].fills[("GND", "In1.Cu")]

    assert gnd.bounds[2] == pytest.approx(126.0)
    assert gnd.contains(Point(120, 88))
    assert not gnd.contains(Point(130, 88))


# ---- later: KiCad 9 net table (P7, Jetson Thor demo) ----

@pytest.mark.skip(reason="P7: KiCad 9 writes nets as numbers + a top-level net table")
def test_kicad9_net_numbers_resolve_to_names():
    tree = parse("""(kicad_pcb
        (net 0 "") (net 1 "GND") (net 2 "SIG")
        (segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net 2))
        (zone (net 1) (net_name "GND") (layer "In1.Cu")
            (filled_polygon (layer "In1.Cu") (pts (xy 0 0) (xy 1 0) (xy 1 1)))))""")
    board = Board.from_s_expression(tree)

    assert board.segments[0].net == "SIG"
    assert ("GND", "In1.Cu") in board.fills
