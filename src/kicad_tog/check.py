"""Geometric reference-plane continuity checks for KiCad board traces."""

from __future__ import annotations

from dataclasses import dataclass

from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, box
from shapely.ops import linemerge, unary_union

from .board import Board

# EPS: distance below which a span "touches" a trace end (float noise).
# MIN_SPAN: spans shorter than this are slivers, dropped (mm).
# PAD_CLEARANCE_MAX: an end span shorter than this is pad clearance, not an
# overhang (mm). Fixture pin spans are 1.75 = pad radius 0.85 + zone
# clearance 0.5 + r 0.4; 2.5 leaves room for slightly bigger pads.
EPS = 1e-6
MIN_SPAN = 0.05
PAD_CLEARANCE_MAX = 2.5

# A copper layer is a plane when its fills (every net) cover at least this
# share of the board area. Net names don't matter: a split power layer is
# still a plane, and the gaps between its islands are what we look for.
PLANE_COVERAGE = 0.5
# Two planes are "equally near" when the farther h is within 10 % of the nearer.
TIE_TOLERANCE = 0.10
# A gap in the farther plane counts one level lower than in the nearest plane.
_FARTHER = {"error": "warning", "warning": "info", "info": "info"}


@dataclass(frozen=True)
class Violation:
    net: str
    layer: str
    kind: str
    severity: str
    start: tuple[float, float]
    end: tuple[float, float]
    length: float
    ref_layer: str | None = None  # None only for "no_reference"


@dataclass(frozen=True)
class Reference:
    layer: str
    h: float
    nearest: bool


def _line_parts(geometry) -> list[LineString]:
    """Extract line components from a Shapely difference/merge result."""
    if geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if hasattr(geometry, "geoms"):
        parts = []
        for item in geometry.geoms:
            parts.extend(_line_parts(item))
        return parts
    return []


def uncovered_spans(
    trace: LineString,
    copper,
    radius: float,
) -> list[LineString]:
    """Return portions of trace not covered by copper within ``radius``.

    Eroding the copper by the radius makes narrow strips and nearby holes
    count as uncovered, while holes farther than the radius remain ignored.
    Returned spans follow the trace's coordinate direction and are ordered
    from its start to its end.
    """
    safe_copper = copper.buffer(-radius)
    difference = trace.difference(safe_copper)

    if difference.is_empty:
        return []

    if difference.geom_type == "MultiLineString":
        difference = linemerge(difference)

    spans = _line_parts(difference)

    # GEOS may return individual pieces in either coordinate direction.
    oriented = []
    for part in spans:
        coords = list(part.coords)
        first = Point(coords[0])
        last = Point(coords[-1])
        if trace.project(first) > trace.project(last):
            coords.reverse()
            part = LineString(coords)
        position = trace.project(Point(part.coords[0]))
        oriented.append((position, part))

    oriented.sort(key=lambda item: item[0])
    return [part for _, part in oriented]


def classify(
    span: LineString,
    trace: LineString,
) -> tuple[str, str] | None:
    """Classify one uncovered span as a split, pad clearance, or overhang."""
    if span.length < MIN_SPAN:
        return None

    start_point = Point(trace.coords[0])
    end_point = Point(trace.coords[-1])
    touches_start = span.distance(start_point) < EPS
    touches_end = span.distance(end_point) < EPS

    if not touches_start and not touches_end:
        return ("split", "error")

    if span.length < PAD_CLEARANCE_MAX:
        return ("pad_clearance", "info")

    return ("edge_overhang", "warning")


class LocalCopper:
    """Hand each trace only the reference copper near it.

    Eroding the whole board's copper once per segment is what makes a real
    board slow (~0.4 s per segment on the 6-layer CM5 demo board). The copper
    is split into its polygons once and indexed in an STRtree; each trace gets
    the polygons that touch its window, clipped to that window.

    The clip cannot change the answer. Erosion distributes over intersection:
    erode(A & W) = erode(A) & erode(W). With a margin of 2r around the trace,
    every trace point stays inside erode(W), so on the trace the clipped and
    the full copper give the same spans.
    """

    def __init__(self, copper) -> None:
        self.parts = list(getattr(copper, "geoms", [copper]))
        self.tree = STRtree(self.parts)

    def near(self, trace: LineString, radius: float) -> MultiPolygon:
        min_x, min_y, max_x, max_y = trace.bounds
        margin = 2.0 * radius
        window = box(min_x - margin, min_y - margin, max_x + margin, max_y + margin)
        nearby = [self.parts[i] for i in self.tree.query(window)]
        return MultiPolygon(nearby).intersection(window)


def _layer_copper(board: Board, layer: str):
    """Union of every net's fill on one layer (empty if there is none)."""
    pours = [
        fill
        for (_net, fill_layer), fill in board.fills.items()
        if fill_layer == layer and not fill.is_empty
    ]
    return unary_union(pours) if pours else None


def plane_layers(board: Board) -> list[str]:
    """Copper layers, in stackup order, whose fills cover >= 50 % of the board."""
    area = board.area()
    if area <= 0:
        return []
    planes = []
    for layer in board.copper_layers():
        copper = _layer_copper(board, layer)
        if copper is not None and copper.area >= PLANE_COVERAGE * area:
            planes.append(layer)
    return planes


def references(board: Board, signal_layer: str, planes: list[str]) -> list[Reference]:
    """The nearest plane above and the nearest below ``signal_layer``.

    Never the signal layer itself: a flood beside a trace is not under it.
    One plane is the nearest (a gap there is an error), the other the farther
    one (a gap counts one level lower), unless their h are within 10 %, when
    both are nearest. An outer layer has at most one side, so one reference.
    """
    order = board.copper_layers()
    if signal_layer not in order:
        raise ValueError(f"Layer {signal_layer!r} is not in the stackup")
    index = order.index(signal_layer)

    above = [layer for layer in order[:index] if layer in planes]
    below = [layer for layer in order[index + 1 :] if layer in planes]
    candidates = ([above[-1]] if above else []) + ([below[0]] if below else [])

    heights = {layer: board.h(signal_layer, layer) for layer in candidates}
    if not heights:
        return []
    nearest_h = min(heights.values())
    return [
        Reference(layer, h, nearest=h <= nearest_h * (1.0 + TIE_TOLERANCE))
        for layer, h in heights.items()
    ]


def _selected(board: Board, nets, signal_layer: str | None):
    selected_nets = set(nets)
    for segment in board.segments:
        if segment.net not in selected_nets:
            continue
        if signal_layer is not None and segment.layer != signal_layer:
            continue
        yield segment


def reference_map(
    board: Board,
    nets,
    signal_layer: str | None = None,
) -> dict[str, list[str]]:
    """Plane -> the signal layers that use it, for the checked segments.

    Planes come in stackup order. The CLI uses it for the report header and
    for one figure per plane.
    """
    planes = plane_layers(board)
    used: dict[str, list[str]] = {}
    for layer in sorted({s.layer for s in _selected(board, nets, signal_layer)}):
        for reference in references(board, layer, planes):
            used.setdefault(reference.layer, []).append(layer)
    order = board.copper_layers()
    return {plane: used[plane] for plane in order if plane in used}


def _check_piece(segment, trace, copper, radius, ref_layer, nearest):
    for uncovered in uncovered_spans(trace, copper.near(trace, radius), radius):
        classification = classify(uncovered, trace)
        if classification is None:
            continue
        kind, severity = classification
        yield Violation(
            net=segment.net,
            layer=segment.layer,
            kind=kind,
            severity=severity if nearest else _FARTHER[severity],
            start=tuple(uncovered.coords[0]),
            end=tuple(uncovered.coords[-1]),
            length=uncovered.length,
            ref_layer=ref_layer,
        )


def check(
    board: Board,
    nets: list[str] | set[str] | tuple[str, ...],
    ref_layer: str | None = None,
    k: float = 3.0,
    signal_layer: str | None = None,
) -> list[Violation]:
    """Check selected nets against their reference plane(s).

    ``ref_layer`` given: every checked segment is compared with all filled
    copper on that layer (GND or a power plane; a gap between two pours is
    still a gap, so it shows up as a split).

    ``ref_layer`` None (auto): each segment is compared with the nearest plane
    above and below its own layer (see ``references``). A segment with no plane
    on either side is one ``no_reference`` error.

    ``signal_layer`` keeps only segments on that layer. On a real board a net
    changes layers through vias, and its segment on In5.Cu says nothing about
    the plane on In1.Cu.
    """
    if ref_layer is not None and signal_layer == ref_layer:
        raise ValueError(f"signal layer and reference layer are both {ref_layer}")

    violations: list[Violation] = []

    if ref_layer is not None:
        copper = _layer_copper(board, ref_layer)
        if copper is None:
            raise ValueError(f"No reference copper found on layer {ref_layer!r}")
        local = LocalCopper(copper)
        for segment in _selected(board, nets, signal_layer):
            radius = segment.width / 2.0 + k * board.h(segment.layer, ref_layer)
            trace = LineString([segment.start, segment.end])
            violations += _check_piece(segment, trace, local, radius, ref_layer, True)
        return violations

    if not board.copper_layers():
        raise ValueError("the board has no stackup, so the dielectric height is unknown")

    planes = plane_layers(board)
    refs_by_layer: dict[str, list[Reference]] = {}
    copper_by_plane: dict[str, LocalCopper] = {}

    for segment in _selected(board, nets, signal_layer):
        if segment.layer not in refs_by_layer:
            refs_by_layer[segment.layer] = references(board, segment.layer, planes)
        trace = LineString([segment.start, segment.end])

        refs = refs_by_layer[segment.layer]
        if not refs:
            violations.append(
                Violation(
                    net=segment.net,
                    layer=segment.layer,
                    kind="no_reference",
                    severity="error",
                    start=segment.start,
                    end=segment.end,
                    length=trace.length,
                    ref_layer=None,
                )
            )
            continue

        for ref in refs:
            if ref.layer not in copper_by_plane:
                copper_by_plane[ref.layer] = LocalCopper(_layer_copper(board, ref.layer))
            radius = segment.width / 2.0 + k * ref.h
            violations += _check_piece(
                segment, trace, copper_by_plane[ref.layer], radius, ref.layer, ref.nearest
            )

    return violations
