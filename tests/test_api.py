"""End-to-end + geometry checks. Run: python -m tests.test_api"""

from __future__ import annotations

import os
import sys
import tempfile

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_PATH"] = _tmp.name

from fastapi.testclient import TestClient

from app.main import app
from app.normalize import (
    normalize,
    point_in_polygon,
    reverse_ring,
    signed_area,
    visual_orientation,
)
from app.svgpath import flatten_subpath, parse

# Using the client as a context manager triggers the lifespan startup,
# which initializes the SQLite schema.
client = TestClient(app)
client.__enter__()
failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" :: {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---- parser ----------------------------------------------------------------
big_sq = "M0 0 L100 0 100 100 0 100 Z"   # visually clockwise in SVG
inner_sq = "M20 20 L80 20 80 80 20 80 Z"  # same direction as outer
inner_dot = "M40 40 L60 40 60 60 40 60 Z"

subs = parse(f"{big_sq} {inner_sq} {inner_dot}")
check("parser: 3 subpaths", len(subs) == 3)
check("parser: closed flags", all(s.closed for s in subs))

# relative + shorthand commands parse
subs2 = parse("M10 10 c10 0 10 10 0 10 s-10 10 0 10 q-5 -5 -10 0 t-10 0 z")
check("parser: relative c/s/q/t", len(subs2) == 1 and len(subs2[0].segments) == 4)

# arc flattening
arc = parse("M50 0 A50 50 0 1 0 150 100 A50 50 0 1 0 50 0 Z")
pts = flatten_subpath(arc[0], 0.1)
check("flatten: arc samples many points", len(pts) > 20, f"got {len(pts)}")
check("flatten: arc closes near start",
      abs(pts[-1][0] - pts[0][0]) < 0.5 and abs(pts[-1][1] - pts[0][1]) < 0.5)

# ---- geometry --------------------------------------------------------------
poly = flatten_subpath(parse(big_sq)[0])
check("geom: cw square area positive", signed_area(poly) > 0)
check("geom: orientation label cw", visual_orientation(signed_area(poly)) == "cw")
rev = reverse_ring(poly)
check("geom: reverse keeps anchor", rev[0] == poly[0] and signed_area(rev) < 0)
check("geom: point-in-polygon",
      point_in_polygon(50, 50, poly) == 1
      and point_in_polygon(200, 200, poly) == 0
      and point_in_polygon(0, 50, poly) == -1)

# ---- normalization: 3-level nesting ---------------------------------------
raw = [flatten_subpath(s) for s in subs]
res = normalize(raw, [True, True, True], outer_orientation="ccw")
roles = [(c.depth, c.role, c.orientation, c.excluded) for c in res.contours]
check("norm: hierarchy outer/hole/outer",
      roles == [(0, "outer", "ccw", False),
                (1, "hole", "cw", False),
                (2, "outer", "ccw", False)],
      str(roles))
parents = [c.parent_index for c in res.contours]
check("norm: parent chain", parents == [None, 0, 1], str(parents))
check("norm: d contains 3 Z subpaths", res.d.count("Z") == 3)
check("norm: bounds", res.bounds == (0.0, 0.0, 100.0, 100.0), str(res.bounds))

# ---- warnings --------------------------------------------------------------
res2 = normalize(
    [flatten_subpath(s) for s in parse("M0 0 L10 0 L10 10 L0 10 Z")] +
    [flatten_subpath(s) for s in parse("M0 0 L50 50")] +
    [flatten_subpath(s) for s in parse("M0 0 L10 0 L0 10 Z")],
    [True, False, True],
)
codes = [w.code for w in res2.warnings]
check("warn: unclosed", "unclosed_contour" in codes, str(codes))
check("warn: degenerate", "degenerate_contour" in codes, str(codes))
check("warn: degenerate excluded",
      any(c.excluded for c in res2.contours))

# bowtie-ish self crossing with nonzero signed area
bow = flatten_subpath(parse("M0 0 L10 0 L10 10 L-5 5 L15 5 L0 10 Z")[0])
res3 = normalize([bow], [True])
check("warn: self-intersection",
      any(w.code == "self_intersection" for w in res3.warnings))

# ---- API -------------------------------------------------------------------
r = client.get("/health")
check("api: health", r.status_code == 200 and r.json()["status"] == "ok")

r = client.post("/paths", json={"d": "", "fill_rule": "nonzero"})
check("api: empty d rejected", r.status_code == 422)

r = client.post("/paths", json={"d": "M1 1 BOGUS 2", "fill_rule": "nonzero"})
check("api: invalid d rejected", r.status_code == 422, str(r.status_code))

r = client.post("/paths", json={"d": big_sq, "fill_rule": "weird"})
check("api: bad fill_rule rejected", r.status_code == 422)

payload = {
    "d": f"{big_sq} {inner_sq} {inner_dot}",
    "fill_rule": "evenodd",
    "metadata": {"author": "alice", "project": "logo", "tags": ["v2"]},
}
r = client.post("/paths", json=payload)
check("api: create 201", r.status_code == 201, r.text)
body = r.json()
pid = body["id"]
check("api: contour_count=3", body["contour_count"] == 3)
check("api: metadata echoed", body["metadata"]["author"] == "alice")

r = client.post(f"/paths/{pid}/normalize", json={"outer_orientation": "ccw"})
check("api: normalize 200", r.status_code == 200, r.text)
norm = r.json()
seq = [(c["index"], c["depth"], c["role"], c["orientation"])
       for c in norm["contours"]]
check("api: original index + hierarchy kept",
      seq == [(0, 0, "outer", "ccw"),
              (1, 1, "hole", "cw"),
              (2, 2, "outer", "ccw")],
      str(seq))
check("api: normalized d present", norm["d"].startswith("M0 0"))

r = client.get(f"/paths/{pid}")
check("api: get 200", r.status_code == 200)
detail = r.json()
check("api: get has original d", detail["original"]["d"] == payload["d"])
check("api: get status normalized", detail["status"] == "normalized")
check("api: metadata preserved on get",
      detail["metadata"]["tags"] == ["v2"])
check("api: normalized embedded", detail["normalized"]["d"] == norm["d"])
check("api: warnings field is list", isinstance(detail["normalized"]["warnings"], list))

r = client.get("/paths/does-not-exist")
check("api: 404", r.status_code == 404)
r = client.post("/paths/does-not-exist/normalize")
check("api: normalize 404", r.status_code == 404)

# normalize without body (defaults)
r = client.post("/paths", json={"d": f"{big_sq} {inner_sq}"})
pid2 = r.json()["id"]
r = client.post(f"/paths/{pid2}/normalize")
check("api: normalize with empty body",
      r.status_code == 200 and r.json()["outer_orientation"] == "ccw")

# re-normalize replaces old result (no duplicated contours)
r = client.post(f"/paths/{pid2}/normalize", json={"outer_orientation": "cw"})
r = client.get(f"/paths/{pid2}")
cs = r.json()["normalized"]["contours"]
check("api: re-normalize replaces rows",
      len(cs) == 2 and all(c["role"] in ("outer", "hole") for c in cs)
      and cs[0]["orientation"] == "cw")

r = client.get("/paths")
check("api: list", r.status_code == 200 and len(r.json()) >= 2)

# unclosed contour produces warning visible through GET
r = client.post("/paths", json={"d": "M0 0 L10 0 L10 10 L0 10"})
pid3 = r.json()["id"]
r = client.post(f"/paths/{pid3}/normalize")
check("api: unclosed warning surfaced",
      any(w["code"] == "unclosed_contour"
          for w in r.json()["warnings"]))

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("ALL TESTS PASSED")
