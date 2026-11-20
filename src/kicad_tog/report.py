"""Text, JSON, and matplotlib reports for kicad-tog violations."""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from shapely.plotting import plot_polygon

if TYPE_CHECKING:
    from .board import Board
    from .check import Violation


SEVERITIES = ("error", "warning", "info")
COLORS: dict[str, str] = {
    "split": "#d62728",
    "edge_overhang": "#e69f00",
    "pad_clearance": "#0072b2",
    "no_reference": "#7b3294",
}

_SEVERITY_ORDER = {severity: index for index, severity in enumerate(SEVERITIES)}
_PLANE_FACE_COLOR = "#dce8df"
_PLANE_EDGE_COLOR = "#91a99a"
_TRACE_COLOR = "#555b66"


def ordered(violations: Iterable[Violation]) -> list[Violation]:
    """Return violations in severity/net/layer/start order without mutation."""
    return sorted(
        violations,
        key=lambda item: (
            _SEVERITY_ORDER[item.severity], item.net, item.layer, item.start
        ),
    )


def summarize(violations: Iterable[Violation]) -> dict[str, int]:
    counts = dict.fromkeys(SEVERITIES, 0)
    for violation in violations:
        counts[violation.severity] += 1
    return counts


def failed(violations: Iterable[Violation]) -> bool:
    return any(violation.severity == "error" for violation in violations)


def summary_line(counts: Mapping[str, int]) -> str:
    errors = counts["error"]
    warnings = counts["warning"]
    info = counts["info"]
    return (
        f"{errors} {'error' if errors == 1 else 'errors'}, "
        f"{warnings} {'warning' if warnings == 1 else 'warnings'}, {info} info"
    )


def _point(point: tuple[float, float]) -> list[float]:
    return [round(point[0], 4), round(point[1], 4)]


def _header(
    *,
    board_name: str,
    ref_layer: str | None,
    nets: Iterable[str],
    k: float,
    signal_layer: str | None = None,
    planes: Sequence[str] = (),
    nets_reason: str | None = None,
) -> str:
    nets = sorted(nets)
    # ref_layer None: the planes were found automatically (check's auto mode).
    reference = ref_layer if ref_layer is not None else f"auto ({', '.join(planes) or 'none'})"
    # nets_reason set: the nets were chosen automatically; say how many and why.
    net_text = f"{len(nets)} ({nets_reason})" if nets_reason else ", ".join(nets)
    header = f"Board: {board_name} | Reference layer: {reference} | Nets: {net_text} | k: {k:g}"
    if signal_layer is not None:
        header += f" | Signal layer: {signal_layer}"
    return header


def to_text(
    violations: Iterable[Violation],
    *,
    board_name: str,
    ref_layer: str | None,
    nets: Iterable[str],
    k: float,
    signal_layer: str | None = None,
    planes: Sequence[str] = (),
    nets_reason: str | None = None,
) -> str:
    items = list(violations)
    lines = [
        _header(
            board_name=board_name,
            ref_layer=ref_layer,
            nets=nets,
            k=k,
            signal_layer=signal_layer,
            planes=planes,
            nets_reason=nets_reason,
        )
    ]
    if not items:
        lines.append("No violations")
    for item in ordered(items):
        sx, sy = item.start
        ex, ey = item.end
        # With automatic references each line names its plane.
        ref = f" ref={item.ref_layer or 'none'}" if ref_layer is None else ""
        lines.append(
            f"{item.severity.upper()} {item.kind} net={item.net} layer={item.layer}{ref} "
            f"{item.length:.2f} mm ({sx:.2f}, {sy:.2f}) -> ({ex:.2f}, {ey:.2f})"
        )
    lines.append(summary_line(summarize(items)))
    return "\n".join(lines) + "\n"


def to_dict(
    violations: Iterable[Violation],
    *,
    board_name: str,
    ref_layer: str | None,
    nets: Iterable[str],
    k: float,
    signal_layer: str | None = None,
    planes: Sequence[str] = (),
    nets_reason: str | None = None,
) -> dict[str, Any]:
    items = list(violations)
    return {
        "board": board_name,
        "ref_layer": ref_layer,  # null: planes found automatically
        "reference_planes": list(planes) if ref_layer is None else [ref_layer],
        "signal_layer": signal_layer,  # null: every layer was checked
        "nets": sorted(nets),
        "net_selection": nets_reason,  # null: given with --nets
        "k": round(k, 4),
        "summary": summarize(items),
        "failed": failed(items),
        "violations": [
            {
                "net": item.net,
                "layer": item.layer,
                "ref_layer": item.ref_layer,
                "kind": item.kind,
                "severity": item.severity,
                "start": _point(item.start),
                "end": _point(item.end),
                "length": round(item.length, 4),
            }
            for item in ordered(items)
        ],
    }


def to_json(
    violations: Iterable[Violation],
    *,
    board_name: str,
    ref_layer: str | None,
    nets: Iterable[str],
    k: float,
    signal_layer: str | None = None,
    planes: Sequence[str] = (),
    nets_reason: str | None = None,
) -> str:
    return json.dumps(
        to_dict(
            violations,
            board_name=board_name,
            ref_layer=ref_layer,
            nets=nets,
            k=k,
            signal_layer=signal_layer,
            planes=planes,
            nets_reason=nets_reason,
        ),
        indent=2,
    ) + "\n"


def plot(
    board: Board,
    violations: Iterable[Violation],
    *,
    nets: Iterable[str],
    ref_layer: str,
    signal_layer: str | None = None,
    trace_layers: Collection[str] | None = None,
) -> Figure:
    """Plot checked traces, reference-layer copper, and violation spans."""
    items = ordered(violations)
    checked_nets = set(nets)
    fig = Figure(figsize=(10, 6), layout="constrained")
    ax = fig.subplots()

    # Every pour on the reference layer is relevant, regardless of its net.
    for (_net, layer), multipolygon in board.fills.items():
        if layer == ref_layer:
            for polygon in multipolygon.geoms:
                plot_polygon(
                    polygon,
                    ax=ax,
                    add_points=False,
                    facecolor=_PLANE_FACE_COLOR,
                    edgecolor=_PLANE_EDGE_COLOR,
                    linewidth=0.55,
                    zorder=1,
                )

    traces = [
        segment
        for segment in board.segments
        if segment.net in checked_nets
        and (signal_layer is None or segment.layer == signal_layer)
        and (trace_layers is None or segment.layer in trace_layers)
    ]
    for segment in traces:
        ax.plot(
            [segment.start[0], segment.end[0]],
            [segment.start[1], segment.end[1]],
            color=_TRACE_COLOR,
            linewidth=1,
            solid_capstyle="round",
            gid="trace",
            zorder=2,
        )

    if traces:
        ax.plot(
            [point for segment in traces for point in (segment.start[0], segment.end[0])],
            [point for segment in traces for point in (segment.start[1], segment.end[1])],
            linestyle="None",
            marker="o",
            markersize=3.5,
            markerfacecolor="white",
            markeredgecolor=_TRACE_COLOR,
            markeredgewidth=0.9,
            gid="endpoint",
            zorder=3,
        )

    legend: dict[str, Line2D] = {}
    for item in items:
        color = COLORS[item.kind]
        ax.plot(
            [item.start[0], item.end[0]],
            [item.start[1], item.end[1]],
            color=color,
            linewidth=3.2,
            solid_capstyle="round",
            gid=item.kind,
            zorder=4,
        )
        if item.kind not in legend:
            legend[item.kind] = Line2D([], [], color=color, linewidth=3.2, label=item.kind)

        if item.severity in ("error", "warning"):
            midpoint = (
                (item.start[0] + item.end[0]) / 2,
                (item.start[1] + item.end[1]) / 2,
            )
            ax.annotate(
                f"{item.length:.2f} mm",
                xy=midpoint,
                xytext=(0, 12),
                textcoords="offset points",
                fontsize=8,
                color=color,
                ha="center",
                va="bottom",
                zorder=5,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 1.2},
            )

    if legend:
        ax.legend(
            handles=list(legend.values()),
            loc="upper left",
            bbox_to_anchor=(1.02, 1),
            borderaxespad=0,
        )

    ax.margins(0.04)
    ax.set_aspect(1.0)
    ax.invert_yaxis()
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.grid(False)
    return fig
