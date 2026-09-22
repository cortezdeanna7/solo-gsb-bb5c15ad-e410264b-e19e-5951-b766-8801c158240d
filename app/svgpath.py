"""SVG path data parsing and curve flattening.

Parses an SVG ``d`` attribute into subpaths (contours) made of absolute
segments, then flattens every curve (cubic/quadratic Bezier, elliptical arc)
into polylines so downstream geometry work (area, containment, intersection)
operates on plain point lists.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Command letter or a float (supports ".5", "1.", scientific notation).
_TOKEN_RE = re.compile(
    r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
)

COMMAND_ARITY = {
    "M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7,
}


class PathParseError(ValueError):
    """Raised when the ``d`` attribute cannot be parsed."""


@dataclass
class Segment:
    """One absolute segment. kind in {L, C, Q, A}; M/Z are structural."""

    kind: str
    values: tuple[float, ...]


@dataclass
class Subpath:
    start: tuple[float, float]
    segments: list[Segment] = field(default_factory=list)
    closed: bool = False


def tokenize(d: str) -> list[str]:
    tokens = _TOKEN_RE.findall(d)
    if not tokens:
        raise PathParseError("path data is empty or contains no commands")
    consumed = "".join(tokens)
    leftover = re.sub(r"[\s,]+", "", d)
    if consumed != leftover:
        raise PathParseError("path data contains invalid characters")
    return tokens


def parse(d: str) -> list[Subpath]:
    """Parse SVG path data into a list of subpaths with absolute segments."""
    tokens = tokenize(d)
    subpaths: list[Subpath] = []
    i = 0
    cmd = ""
    cx = cy = 0.0          # current point
    sx = sy = 0.0          # subpath start point
    prev_cubic_ctrl: tuple[float, float] | None = None
    prev_quad_ctrl: tuple[float, float] | None = None
    current: Subpath | None = None

    def is_cmd(tok: str) -> bool:
        return len(tok) == 1 and tok.isalpha()

    def num() -> float:
        nonlocal i
        if i >= len(tokens) or is_cmd(tokens[i]):
            raise PathParseError(f"command '{cmd}' expects more parameters")
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        if is_cmd(tokens[i]):
            cmd = tokens[i]
            i += 1
        elif cmd == "":
            raise PathParseError("path data must start with a command")
        elif cmd in "Mm":
            # Implicit extra coordinate pairs after M are treated as L.
            cmd = "L" if cmd == "M" else "l"
        # Z takes no parameters and never repeats implicitly.
        if cmd in "Zz":
            if current is not None:
                current.closed = True
                subpaths.append(current)
            cx, cy = sx, sy
            current = None
            prev_cubic_ctrl = prev_quad_ctrl = None
            cmd = ""
            continue
        if cmd == "":
            raise PathParseError("missing command before parameters")

        absolute = cmd.isupper()
        C = cmd.upper()
        if C not in COMMAND_ARITY:
            raise PathParseError(f"unsupported command '{cmd}'")

        def px(v: float) -> float:
            return v if absolute else cx + v

        def py(v: float) -> float:
            return v if absolute else cy + v

        if C == "M":
            x, y = px(num()), py(num())
            if current is not None:
                subpaths.append(current)
            current = Subpath(start=(x, y))
            cx = sx = x
            cy = sy = y
            prev_cubic_ctrl = prev_quad_ctrl = None
        elif C == "L":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            x, y = px(num()), py(num())
            current.segments.append(Segment("L", (x, y)))
            cx, cy = x, y
            prev_cubic_ctrl = prev_quad_ctrl = None
        elif C == "H":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            x = px(num())
            current.segments.append(Segment("L", (x, cy)))
            cx = x
            prev_cubic_ctrl = prev_quad_ctrl = None
        elif C == "V":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            y = py(num())
            current.segments.append(Segment("L", (cx, y)))
            cy = y
            prev_cubic_ctrl = prev_quad_ctrl = None
        elif C == "C":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            x1, y1 = px(num()), py(num())
            x2, y2 = px(num()), py(num())
            x, y = px(num()), py(num())
            current.segments.append(Segment("C", (x1, y1, x2, y2, x, y)))
            prev_cubic_ctrl = (x2, y2)
            prev_quad_ctrl = None
            cx, cy = x, y
        elif C == "S":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            if prev_cubic_ctrl is not None:
                x1, y1 = 2 * cx - prev_cubic_ctrl[0], 2 * cy - prev_cubic_ctrl[1]
            else:
                x1, y1 = cx, cy
            x2, y2 = px(num()), py(num())
            x, y = px(num()), py(num())
            current.segments.append(Segment("C", (x1, y1, x2, y2, x, y)))
            prev_cubic_ctrl = (x2, y2)
            prev_quad_ctrl = None
            cx, cy = x, y
        elif C == "Q":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            x1, y1 = px(num()), py(num())
            x, y = px(num()), py(num())
            current.segments.append(Segment("Q", (x1, y1, x, y)))
            prev_quad_ctrl = (x1, y1)
            prev_cubic_ctrl = None
            cx, cy = x, y
        elif C == "T":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            if prev_quad_ctrl is not None:
                x1, y1 = 2 * cx - prev_quad_ctrl[0], 2 * cy - prev_quad_ctrl[1]
            else:
                x1, y1 = cx, cy
            x, y = px(num()), py(num())
            current.segments.append(Segment("Q", (x1, y1, x, y)))
            prev_quad_ctrl = (x1, y1)
            prev_cubic_ctrl = None
            cx, cy = x, y
        elif C == "A":
            if current is None:
                raise PathParseError(f"'{cmd}' before any 'M'")
            rx, ry = abs(num()), abs(num())
            rot = num()
            laf, sf = num(), num()
            if laf not in (0.0, 1.0) or sf not in (0.0, 1.0):
                raise PathParseError("arc flags must be 0 or 1")
            x, y = px(num()), py(num())
            current.segments.append(
                Segment("A", (rx, ry, rot, laf, sf, x, y))
            )
            prev_cubic_ctrl = prev_quad_ctrl = None
            cx, cy = x, y

    if current is not None:
        subpaths.append(current)
    return subpaths


# ---------------------------------------------------------------------------
# Flattening: curves -> polylines
# ---------------------------------------------------------------------------

def _flatten_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    tol: float,
    out: list[tuple[float, float]],
    depth: int = 0,
) -> None:
    """Recursive de Casteljau subdivision until the curve is flat enough."""
    if depth >= 20 or _cubic_flat_enough(p0, p1, p2, p3, tol):
        out.append(p3)
        return
    p01 = _mid(p0, p1)
    p12 = _mid(p1, p2)
    p23 = _mid(p2, p3)
    p012 = _mid(p01, p12)
    p123 = _mid(p12, p23)
    p0123 = _mid(p012, p123)
    _flatten_cubic(p0, p01, p012, p0123, tol, out, depth + 1)
    _flatten_cubic(p0123, p123, p23, p3, tol, out, depth + 1)


def _mid(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _cubic_flat_enough(p0, p1, p2, p3, tol) -> bool:
    # Distance of control points from the chord p0-p3.
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-12:
        return (
            math.hypot(p1[0] - p0[0], p1[1] - p0[1]) <= tol
            and math.hypot(p2[0] - p0[0], p2[1] - p0[1]) <= tol
        )
    d1 = abs(dy * p1[0] - dx * p1[1] + p3[0] * p0[1] - p3[1] * p0[0]) / chord
    d2 = abs(dy * p2[0] - dx * p2[1] + p3[0] * p0[1] - p3[1] * p0[0]) / chord
    return (d1 + d2) <= tol


def _flatten_arc(
    x1: float, y1: float,
    rx: float, ry: float,
    rot_deg: float, large_arc: float, sweep: float,
    x2: float, y2: float,
    tol: float,
    out: list[tuple[float, float]],
) -> None:
    """Flatten an elliptical arc (SVG spec F.6.5 endpoint -> center)."""
    if (abs(x1 - x2) < 1e-12 and abs(y1 - y2) < 1e-12) or rx == 0 or ry == 0:
        out.append((x2, y2))
        return

    phi = math.radians(rot_deg % 360.0)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # Correct out-of-range radii (F.6.6).
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s

    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den > 0 else 0.0
    if bool(large_arc) == bool(sweep):
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        mag = math.hypot(ux, uy) * math.hypot(vx, vy)
        ang = math.acos(max(-1.0, min(1.0, dot / mag)))
        if ux * vy - uy * vx < 0:
            ang = -ang
        return ang

    theta1 = angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle(
        (x1p - cxp) / rx, (y1p - cyp) / ry,
        (-x1p - cxp) / rx, (-y1p - cyp) / ry,
    )
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi

    # Step size so the chord stays within tol of the arc.
    r_max = max(rx, ry)
    if tol > 0 and r_max > tol:
        step = 2.0 * math.acos(max(-1.0, 1.0 - tol / r_max))
        step = max(step, 1e-3)
    else:
        step = math.pi / 8.0
    n = max(1, int(math.ceil(abs(dtheta) / step)))
    for k in range(1, n + 1):
        t = theta1 + dtheta * k / n
        xp, yp = rx * math.cos(t), ry * math.sin(t)
        out.append((
            cos_phi * xp - sin_phi * yp + cx,
            sin_phi * xp + cos_phi * yp + cy,
        ))


def flatten_subpath(
    subpath: Subpath, tol: float = 0.1
) -> list[tuple[float, float]]:
    """Flatten one subpath to a polyline (list of (x, y) points)."""
    points: list[tuple[float, float]] = [subpath.start]
    cx, cy = subpath.start
    for seg in subpath.segments:
        v = seg.values
        if seg.kind == "L":
            points.append((v[0], v[1]))
        elif seg.kind == "C":
            _flatten_cubic((cx, cy), (v[0], v[1]), (v[2], v[3]), (v[4], v[5]),
                           tol, points)
        elif seg.kind == "Q":
            # Elevate quadratic to cubic.
            c1 = (cx + 2.0 / 3.0 * (v[0] - cx), cy + 2.0 / 3.0 * (v[1] - cy))
            c2 = (v[2] + 2.0 / 3.0 * (v[0] - v[2]),
                  v[3] + 2.0 / 3.0 * (v[1] - v[3]))
            _flatten_cubic((cx, cy), c1, c2, (v[2], v[3]), tol, points)
        elif seg.kind == "A":
            _flatten_arc(cx, cy, v[0], v[1], v[2], v[3], v[4], v[5], v[6],
                         tol, points)
        cx, cy = points[-1]
    if subpath.closed and len(points) > 1:
        # Drop the duplicated closing point; closure is implied.
        if (abs(points[-1][0] - points[0][0]) < 1e-9
                and abs(points[-1][1] - points[0][1]) < 1e-9):
            points.pop()
    return points
