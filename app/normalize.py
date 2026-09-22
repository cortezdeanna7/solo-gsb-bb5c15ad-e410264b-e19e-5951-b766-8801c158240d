"""Contour normalization: hierarchy classification + winding rewrite.

Geometry works in SVG coordinate space (y axis points down), so by the
shoelace formula a *visually clockwise* ring has a positive signed area.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]

EPS = 1e-9


@dataclass
class WarningItem:
    code: str
    message: str
    contour_index: int | None = None


@dataclass
class ContourResult:
    index: int
    parent_index: int | None
    depth: int
    role: str                       # "outer" | "hole"
    orientation: str                # final visual orientation: "cw" | "ccw"
    signed_area: float
    point_count: int
    d: str
    excluded: bool = False


@dataclass
class NormalizationResult:
    d: str
    outer_orientation: str
    contours: list[ContourResult]
    warnings: list[WarningItem] = field(default_factory=list)
    bounds: tuple[float, float, float, float] | None = None  # minx,miny,maxx,maxy


def signed_area(points: list[Point]) -> float:
    """Shoelace signed area. >0 means visually clockwise in SVG (y-down)."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def visual_orientation(area: float) -> str:
    if area > 0:
        return "cw"
    if area < 0:
        return "ccw"
    return "none"


def _dedupe(points: list[Point], tol: float = 1e-9) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append(p)
    # Remove a trailing point identical to the first (closure duplicated).
    if len(out) > 1 and math.hypot(out[-1][0] - out[0][0],
                                   out[-1][1] - out[0][1]) <= tol:
        out.pop()
    return out


def point_in_polygon(px: float, py: float, poly: list[Point]) -> int:
    """Return 1 inside, 0 outside, -1 on boundary (ray casting)."""
    n = len(poly)
    if n < 3:
        return 0
    inside = False
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) < EPS:
            seg_len2 = (bx - ax) ** 2 + (by - ay) ** 2
            if seg_len2 > 0:
                t = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / seg_len2
                if -EPS <= t <= 1 + EPS:
                    return -1
        if (ay > py) != (by > py):
            x_cross = ax + (py - ay) * (bx - ax) / (by - ay)
            if x_cross > px:
                inside = not inside
    return 1 if inside else 0


def _contour_contains(test_poly: list[Point], poly: list[Point]) -> bool:
    """True if test_poly lies inside poly, tolerating boundary vertices."""
    for px, py in test_poly:
        r = point_in_polygon(px, py, poly)
        if r == 1:
            return True
        if r == 0:
            return False
        # r == -1: vertex on the boundary, try the next vertex.
    # Every vertex lies on the boundary (coincident contour): treat as
    # contained so nesting still alternates.
    return True


def _segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (p4[0] - p3[0], p4[1] - p3[1])
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < EPS:
        return False  # parallel/collinear, ignored for proper crossings
    t = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / denom
    u = ((p3[0] - p1[0]) * d1[1] - (p3[1] - p1[1]) * d1[0]) / denom
    return EPS < t < 1 - EPS and EPS < u < 1 - EPS


def find_self_intersections(poly: list[Point], cap: int = 20,
                            check_cap: int = 250_000):
    """Return (crossing_pairs, truncated) over non-adjacent edges."""
    n = len(poly)
    pairs: list[tuple[int, int]] = []
    checks = 0
    truncated = False
    if n < 4:
        return pairs, truncated
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # adjacent through the ring closure
            if checks >= check_cap:
                truncated = True
                return pairs, truncated
            checks += 1
            c, dpt = poly[j], poly[(j + 1) % n]
            if _segments_intersect(a, b, c, dpt):
                pairs.append((i, j))
                if len(pairs) >= cap:
                    truncated = True
                    return pairs, truncated
    return pairs, truncated


def reverse_ring(points: list[Point]) -> list[Point]:
    """Reverse winding while keeping the first anchor point fixed."""
    if len(points) <= 1:
        return list(points)
    return [points[0]] + list(reversed(points[1:]))


def _fmt(v: float, precision: int) -> str:
    s = f"{v:.{precision}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def ring_to_d(points: list[Point], precision: int) -> str:
    head = f"M{_fmt(points[0][0], precision)} {_fmt(points[0][1], precision)}"
    rest = " ".join(
        f"L{_fmt(x, precision)} {_fmt(y, precision)}" for x, y in points[1:]
    )
    return f"{head} {rest} Z" if rest else f"{head} Z"


def normalize(
    raw_contours: list[list[Point]],
    closed_flags: list[bool],
    outer_orientation: str = "ccw",
    precision: int = 3,
) -> NormalizationResult:
    """Classify contours by containment depth and rewrite their winding.

    Even depth -> outer ring (``outer_orientation``), odd depth -> hole
    (opposite orientation).
    """
    warnings: list[WarningItem] = []
    contours_in: list[list[Point] | None] = []

    for idx, raw in enumerate(raw_contours):
        pts = _dedupe(raw)
        if not closed_flags[idx] and len(pts) >= 2:
            warnings.append(WarningItem(
                "unclosed_contour",
                f"contour {idx} is not closed (missing 'Z'); "
                "it was closed automatically for processing",
                idx,
            ))
        removed = len(raw) - len(pts)
        if removed > 0:
            warnings.append(WarningItem(
                "duplicate_points",
                f"contour {idx}: removed {removed} consecutive duplicate "
                "point(s)",
                idx,
            ))
        if len(pts) < 3 or abs(signed_area(pts)) < 1e-9:
            warnings.append(WarningItem(
                "degenerate_contour",
                f"contour {idx} has fewer than 3 distinct points or zero "
                "area; excluded from normalized output",
                idx,
            ))
            contours_in.append(None)
        else:
            crossings, truncated = find_self_intersections(pts)
            for a, b in crossings:
                warnings.append(WarningItem(
                    "self_intersection",
                    f"contour {idx}: edges {a} and {b} cross; containment "
                    "classification may be unreliable",
                    idx,
                ))
            if truncated:
                warnings.append(WarningItem(
                    "self_intersection_check_truncated",
                    f"contour {idx}: too many edge pairs; self-intersection "
                    "scan stopped early",
                    idx,
                ))
            contours_in.append(pts)

    n = len(raw_contours)
    valid = [i for i in range(n) if contours_in[i] is not None]

    # Containment matrix: containers[i] = set of valid contour indexes
    # that enclose contour i.
    containers: list[set[int]] = [set() for _ in range(n)]
    for a in valid:
        for b in valid:
            if a == b:
                continue
            if _contour_contains(contours_in[a], contours_in[b]):
                containers[a].add(b)

    hole_orientation = "cw" if outer_orientation == "ccw" else "ccw"
    results: list[ContourResult] = []
    d_parts: list[str] = []
    minx = miny = math.inf
    maxx = maxy = -math.inf

    for idx in range(n):
        pts = contours_in[idx]
        if pts is None:
            results.append(ContourResult(
                index=idx, parent_index=None, depth=0, role="unknown",
                orientation="none", signed_area=0.0, point_count=0, d="",
                excluded=True,
            ))
            continue
        depth = len(containers[idx])
        role = "outer" if depth % 2 == 0 else "hole"
        want = outer_orientation if role == "outer" else hole_orientation
        area = signed_area(pts)
        if visual_orientation(area) != want:
            pts = reverse_ring(pts)
            area = signed_area(pts)
        parent = None
        if containers[idx]:
            # Immediate parent = smallest-area enclosing contour.
            parent = min(containers[idx],
                         key=lambda j: abs(signed_area(contours_in[j])))
        d = ring_to_d(pts, precision)
        d_parts.append(d)
        for x, y in pts:
            minx, maxx = min(minx, x), max(maxx, x)
            miny, maxy = min(miny, y), max(maxy, y)
        results.append(ContourResult(
            index=idx, parent_index=parent, depth=depth, role=role,
            orientation=visual_orientation(area), signed_area=area,
            point_count=len(pts), d=d,
        ))

    if not valid:
        warnings.append(WarningItem(
            "empty_path", "no usable contours after normalization", None,
        ))

    bounds = None if not d_parts else (minx, miny, maxx, maxy)
    return NormalizationResult(
        d=" ".join(d_parts), outer_orientation=outer_orientation,
        contours=results, warnings=warnings, bounds=bounds,
    )
