"""Geometric reference-plane continuity checks for KiCad board traces."""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString, Point
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


@dataclass(frozen=True)
class Violation:
    net: str
    layer: str
    kind: str
    severity: str
    start: tuple[float, float]
    end: tuple[float, float]
    length: float


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


def check(
    board: Board,
    nets: list[str] | set[str] | tuple[str, ...],
    ref_layer: str,
    k: float = 3.0,
) -> list[Violation]:
    """Check selected nets against all filled copper on ``ref_layer``.

    Every net's fill on the reference layer counts (GND or a power plane):
    a gap between two pours is still a gap, so it shows up as a split.
    """
    pours = [
        fill
        for (_net, layer), fill in board.fills.items()
        if layer == ref_layer and not fill.is_empty
    ]
    if not pours:
        raise ValueError(f"No reference copper found on layer {ref_layer!r}")
    copper = unary_union(pours)

    selected_nets = set(nets)
    violations: list[Violation] = []

    for segment in board.segments:
        if segment.net not in selected_nets:
            continue

        height = board.h(segment.layer, ref_layer)
        radius = segment.width / 2.0 + k * height
        trace = LineString([segment.start, segment.end])

        for uncovered in uncovered_spans(trace, copper, radius):
            classification = classify(uncovered, trace)
            if classification is None:
                continue

            kind, severity = classification
            violations.append(
                Violation(
                    net=segment.net,
                    layer=segment.layer,
                    kind=kind,
                    severity=severity,
                    start=tuple(uncovered.coords[0]),
                    end=tuple(uncovered.coords[-1]),
                    length=uncovered.length,
                )
            )

    return violations
