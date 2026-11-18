"""KiCad-specific conversion from S-expressions to board data structures."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

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
    def from_s_expression(cls, node: list) -> "Segment":
        """Build one Segment from a KiCad ``(segment ...)`` node."""
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
            net=net_node[1],
        )


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


@dataclass
class Board:
    segments: list[Segment]
    fills: dict[tuple[str, str], MultiPolygon]
    stackup: list

    @classmethod
    def from_s_expression(cls, tree: list) -> "Board":
        """Extract tracks, filled zone copper, and stackup from a board tree."""
        segments = [
            Segment.from_s_expression(segment_node)
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
            net = net_node[1]

            for filled_polygon in filled_polygons:
                # The zone may cover several layers. Each filled_polygon names
                # the layer it actually represents, so use that layer here.
                layer_node = _required_child(filled_polygon, "layer")
                points_node = _required_child(filled_polygon, "pts")
                points = [
                    (float(xy_node[1]), float(xy_node[2]))
                    for xy_node in children(points_node, "xy")
                ]

                # KiCad's filled_polygon is already a simple outline: holes are
                # represented by the outline itself, so Polygon(points) is enough.
                polygon = Polygon(points)
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

        return cls(segments=segments, fills=fills, stackup=stackup)

    @classmethod
    def load(cls, path: str | Path) -> "Board":
        """Read and parse a KiCad PCB file, then convert it to a Board."""
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_s_expression(parse(text))

    def h(self, signal_layer: str, ref_layer: str) -> float:
        """Return dielectric height between a signal layer and its reference."""
        return dielectric_height(self.stackup, signal_layer, ref_layer)
