"""KiCad-specific conversion from S-expressions to board data structures."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from .s_expression import child, children, parse


def _required_child(node: list, tag: str) -> list:
    """Return a required child node, with a useful error for malformed input."""
    result = child(node, tag)
    if result is None:
        raise ValueError(f"missing ({tag} ...) node")
    return result


def _as_multipolygon(geometry) -> MultiPolygon:
    """Normalize a Shapely union result to MultiPolygon, even for one piece."""
    if isinstance(geometry, MultiPolygon):
        return geometry
    if isinstance(geometry, Polygon):
        return MultiPolygon([geometry])

    # unary_union of polygons normally returns Polygon or MultiPolygon. This
    # fallback also keeps the public fills type stable if it returns a
    # GeometryCollection (for example, after degenerate input is encountered).
    polygons = []
    for part in getattr(geometry, "geoms", ()):
        if isinstance(part, Polygon):
            polygons.append(part)
        elif isinstance(part, MultiPolygon):
            polygons.extend(part.geoms)
    return MultiPolygon(polygons)


@dataclass(frozen=True)
class Segment:
    start: tuple[float, float]
    end: tuple[float, float]
    width: float
    layer: str
    net: str

    @classmethod
    def from_s_expression(
        cls, node: list, net_names: dict[str, str] | None = None
    ) -> "Segment":
        """Build one Segment from a KiCad ``(segment ...)`` node.

        ``net_names`` maps KiCad 9 net numbers to names (see ``net_table``).
        KiCad 10 writes the name itself, which is then used as is.
        """
        start_node = _required_child(node, "start")
        end_node = _required_child(node, "end")
        width_node = _required_child(node, "width")
        layer_node = _required_child(node, "layer")
        net_node = _required_child(node, "net")

        # S-expression atoms are strings, including numeric values.
        return cls(
            start=(float(start_node[1]), float(start_node[2])),
            end=(float(end_node[1]), float(end_node[2])),
            width=float(width_node[1]),
            layer=layer_node[1],
            net=_net_name(net_node, net_names),
        )


def net_table(tree: list) -> dict[str, str]:
    """Map net numbers to names from a KiCad 9 top-level net table.

    KiCad 9 writes ``(net 1 "GND")`` once at the top and then ``(net 1)`` on
    every segment and zone. KiCad 10 writes ``(net "GND")`` everywhere and has
    no table, so the result is empty and names pass through unchanged.
    """
    return {
        node[1]: node[2]
        for node in children(tree, "net")
        if len(node) > 2
    }


def _net_name(net_node: list, net_names: dict[str, str] | None) -> str:
    """Resolve a ``(net ...)`` atom: a KiCad 9 number or a KiCad 10 name."""
    value = net_node[1]
    if net_names:
        return net_names.get(value, value)
    return value


def dielectric_height(stackup: list, layer_a: str, layer_b: str) -> float:
    """Sum dielectric thicknesses between two copper layers in either order."""
    layers = children(stackup, "layer")

    # A stackup layer's name is its first atom after ``layer``; its type and
    # thicknesses are child nodes, e.g. (layer "F.Cu" (type "copper") ...).
    layer_indices = {
        layer[1]: index
        for index, layer in enumerate(layers)
        if len(layer) > 1
    }

    for name in (layer_a, layer_b):
        if name not in layer_indices:
            raise ValueError(f"Layer {name!r} is not in the stackup")

    first, last = sorted((layer_indices[layer_a], layer_indices[layer_b]))
    height = 0.0

    # Only layers strictly between the two copper layers contribute. Do not
    # add copper thickness itself. A prepreg may contain multiple thickness
    # nodes, so collect all of them rather than taking only the first child.
    for layer in layers[first + 1 : last]:
        type_node = child(layer, "type")
        if type_node is not None and type_node[1] == "copper":
            continue

        height += sum(
            float(thickness_node[1])
            for thickness_node in children(layer, "thickness")
        )

    return height


Bounds = tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)

_OUTLINE_SHAPES = ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly")


def _point(node: list) -> tuple[float, float]:
    return (float(node[1]), float(node[2]))


def outline_bounds(tree: list) -> Bounds | None:
    """Bounding box of the board outline (board-level shapes on Edge.Cuts).

    A bounding box, not the outline polygon: notches and rounded corners count
    as board area. Good enough for the 50 % plane-coverage rule; tracing the
    real outline is a listed limit. Arcs contribute their start, mid and end.
    """
    points: list[tuple[float, float]] = []
    for node in tree[1:]:
        if not isinstance(node, list) or not node or node[0] not in _OUTLINE_SHAPES:
            continue
        layer_node = child(node, "layer")
        if layer_node is None or layer_node[1] != "Edge.Cuts":
            continue

        if node[0] == "gr_circle":
            cx, cy = _point(_required_child(node, "center"))
            ex, ey = _point(_required_child(node, "end"))
            r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            points += [(cx - r, cy - r), (cx + r, cy + r)]
        elif node[0] == "gr_poly":
            points += [_point(xy) for xy in children(_required_child(node, "pts"), "xy")]
        else:
            points += [_point(n) for tag in ("start", "mid", "end") if (n := child(node, tag))]

    if not points:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class Board:
    segments: list[Segment]
    fills: dict[tuple[str, str], MultiPolygon]
    stackup: list
    outline: Bounds | None = None

    @classmethod
    def from_s_expression(cls, tree: list) -> "Board":
        """Extract tracks, filled zone copper, and stackup from a board tree."""
        net_names = net_table(tree)
        segments = [
            Segment.from_s_expression(segment_node, net_names)
            for segment_node in children(tree, "segment")
        ]

        # Keep the original polygons grouped by (net, filled-copper layer).
        # Multiple zones can contribute copper to the same key.
        polygons_by_net_layer: dict[tuple[str, str], list[Polygon]] = defaultdict(list)

        for zone in children(tree, "zone"):
            filled_polygons = children(zone, "filled_polygon")
            if not filled_polygons:
                # Rule areas / keepouts may have neither a net nor filled copper.
                continue

            net_node = child(zone, "net")
            if net_node is None:
                continue
            net = _net_name(net_node, net_names)

            for filled_polygon in filled_polygons:
                # The zone may cover several layers. Each filled_polygon names
                # the layer it actually represents, so use that layer here.
                layer_node = _required_child(filled_polygon, "layer")
                points_node = _required_child(filled_polygon, "pts")
                points = [
                    (float(xy_node[1]), float(xy_node[2]))
                    for xy_node in children(points_node, "xy")
                ]

                # KiCad writes holes into the outline itself: the ring runs into
                # each hole and back along a zero-width bridge. That ring touches
                # itself, so the Polygon is invalid, and Shapely's results on
                # invalid input are unreliable (false splits on real boards).
                # make_valid turns it into proper polygons with holes.
                polygon = make_valid(Polygon(points))
                if not polygon.is_empty:
                    polygons_by_net_layer[(net, layer_node[1])].append(polygon)

        fills = {
            key: _as_multipolygon(unary_union(polygons))
            for key, polygons in polygons_by_net_layer.items()
        }

        setup_node = child(tree, "setup")
        stackup = child(setup_node, "stackup") if setup_node is not None else None
        if stackup is None:
            stackup = []

        return cls(
            segments=segments,
            fills=fills,
            stackup=stackup,
            outline=outline_bounds(tree),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Board":
        """Read and parse a KiCad PCB file, then convert it to a Board."""
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_s_expression(parse(text))

    def h(self, signal_layer: str, ref_layer: str) -> float:
        """Return dielectric height between a signal layer and its reference."""
        return dielectric_height(self.stackup, signal_layer, ref_layer)

    def copper_layers(self) -> list[str]:
        """Copper layer names in stackup order, top to bottom."""
        return [
            layer[1]
            for layer in children(self.stackup, "layer")
            if len(layer) > 1
            and (type_node := child(layer, "type")) is not None
            and type_node[1] == "copper"
        ]

    def area(self) -> float:
        """Board area for the plane-coverage rule (mm²).

        The outline's bounding box; without an outline, the bounding box of all
        filled copper, so a board with no Edge.Cuts still gets a number.
        """
        bounds = self.outline
        if bounds is None:
            fills = [fill for fill in self.fills.values() if not fill.is_empty]
            if not fills:
                return 0.0
            bounds = unary_union(fills).bounds
        min_x, min_y, max_x, max_y = bounds
        return (max_x - min_x) * (max_y - min_y)
