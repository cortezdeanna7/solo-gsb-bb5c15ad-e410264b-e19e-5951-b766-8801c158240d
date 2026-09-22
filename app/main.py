"""SVG contour normalization API.

Endpoints
---------
POST   /paths              Store SVG path data + fill rule (+ metadata)
POST   /paths/{id}/normalize  Classify outer/hole contours and fix winding
GET    /paths/{id}         Fetch original path, normalization and warnings
GET    /paths              List stored paths
GET    /health             Liveness probe
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from sqlite3 import Connection

from . import db
from .models import (
    NormalizeRequest,
    NormalizedOut,
    PathCreate,
    PathCreated,
    PathDetail,
    PathSummary,
)
from .normalize import normalize
from .svgpath import PathParseError, flatten_subpath, parse


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # auto-initialize schema (and data directory) on startup
    yield


app = FastAPI(
    title="SVG Contour Normalization API",
    version="1.0.0",
    description="Classifies SVG subpaths into outer rings / holes by "
    "containment hierarchy and canonicalizes their winding direction.",
    lifespan=lifespan,
)


def get_conn() -> Connection:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contour_count(d: str) -> int:
    return len(parse(d))


def _load_normalized(conn: Connection, path_id: str):
    row = conn.execute(
        "SELECT normalized_d, outer_orientation, bounds FROM paths WHERE id = ?",
        (path_id,),
    ).fetchone()
    if row is None or row["normalized_d"] is None:
        return None
    contour_rows = conn.execute(
        "SELECT * FROM contours WHERE path_id = ? ORDER BY contour_index",
        (path_id,),
    ).fetchall()
    warning_rows = conn.execute(
        "SELECT code, message, contour_index FROM warnings WHERE path_id = ? "
        "ORDER BY id",
        (path_id,),
    ).fetchall()
    return NormalizedOut(
        d=row["normalized_d"],
        outer_orientation=row["outer_orientation"],
        contours=[
            {
                "index": r["contour_index"],
                "parent_index": r["parent_index"],
                "depth": r["depth"],
                "role": r["role"],
                "orientation": r["orientation"],
                "signed_area": r["signed_area"],
                "point_count": r["point_count"],
                "d": r["d"],
                "excluded": bool(r["excluded"]),
            }
            for r in contour_rows
        ],
        warnings=[dict(r) for r in warning_rows],
        bounds=json.loads(row["bounds"]) if row["bounds"] else None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/paths", response_model=PathCreated, status_code=201)
def create_path(payload: PathCreate, conn: Connection = Depends(get_conn)):
    d = payload.d.strip()
    try:
        contour_count = len(parse(d))
    except PathParseError as exc:
        raise HTTPException(status_code=422, detail=f"invalid SVG path data: {exc}")

    path_id = uuid.uuid4().hex
    now = utcnow()
    conn.execute(
        "INSERT INTO paths (id, d, fill_rule, metadata, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'received', ?, ?)",
        (path_id, d, payload.fill_rule,
         json.dumps(payload.metadata, ensure_ascii=False), now, now),
    )
    conn.commit()
    return PathCreated(
        id=path_id, status="received", fill_rule=payload.fill_rule,
        metadata=payload.metadata, contour_count=contour_count,
        created_at=datetime.fromisoformat(now),
    )


@app.get("/paths", response_model=list[PathSummary])
def list_paths(conn: Connection = Depends(get_conn)):
    rows = conn.execute(
        "SELECT p.id, p.status, p.fill_rule, p.created_at, "
        "(SELECT COUNT(*) FROM contours c WHERE c.path_id = p.id) AS n "
        "FROM paths p ORDER BY p.created_at"
    ).fetchall()
    # contour_count from stored normalization; fall back to parse count only
    # for unnormalized paths we recompute from d.
    out = []
    for r in rows:
        count = r["n"]
        if r["status"] == "received":
            d_row = conn.execute("SELECT d FROM paths WHERE id = ?",
                                 (r["id"],)).fetchone()
            count = len(parse(d_row["d"]))
        out.append(PathSummary(
            id=r["id"], status=r["status"], fill_rule=r["fill_rule"],
            contour_count=count, created_at=datetime.fromisoformat(r["created_at"]),
        ))
    return out


@app.post("/paths/{path_id}/normalize", response_model=NormalizedOut)
def normalize_path(
    path_id: str,
    payload: NormalizeRequest | None = None,
    conn: Connection = Depends(get_conn),
):
    payload = payload or NormalizeRequest()
    row = conn.execute("SELECT * FROM paths WHERE id = ?", (path_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="path not found")

    try:
        subpaths = parse(row["d"])
    except PathParseError as exc:  # stored data was validated on ingest
        raise HTTPException(status_code=422,
                            detail=f"invalid SVG path data: {exc}")

    raw_contours = [
        flatten_subpath(sp, payload.flatten_tolerance) for sp in subpaths
    ]
    closed_flags = [sp.closed for sp in subpaths]

    result = normalize(
        raw_contours, closed_flags,
        outer_orientation=payload.outer_orientation,
        precision=payload.precision,
    )

    # Replace any previous normalization.
    conn.execute("DELETE FROM contours WHERE path_id = ?", (path_id,))
    conn.execute("DELETE FROM warnings WHERE path_id = ?", (path_id,))
    for c in result.contours:
        conn.execute(
            "INSERT INTO contours (path_id, contour_index, parent_index, "
            "depth, role, orientation, signed_area, point_count, d, excluded) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (path_id, c.index, c.parent_index, c.depth, c.role,
             c.orientation, c.signed_area, c.point_count, c.d,
             1 if c.excluded else 0),
        )
    for w in result.warnings:
        conn.execute(
            "INSERT INTO warnings (path_id, contour_index, code, message) "
            "VALUES (?, ?, ?, ?)",
            (path_id, w.contour_index, w.code, w.message),
        )
    now = utcnow()
    conn.execute(
        "UPDATE paths SET status = 'normalized', normalized_d = ?, "
        "outer_orientation = ?, bounds = ?, updated_at = ? WHERE id = ?",
        (result.d, result.outer_orientation,
         json.dumps(result.bounds) if result.bounds else None,
         now, path_id),
    )
    conn.commit()

    return NormalizedOut(
        d=result.d,
        outer_orientation=result.outer_orientation,
        contours=[
            {
                "index": c.index, "parent_index": c.parent_index,
                "depth": c.depth, "role": c.role, "orientation": c.orientation,
                "signed_area": c.signed_area, "point_count": c.point_count,
                "d": c.d, "excluded": c.excluded,
            }
            for c in result.contours
        ],
        warnings=[
            {"code": w.code, "message": w.message,
             "contour_index": w.contour_index}
            for w in result.warnings
        ],
        bounds=result.bounds,
    )


@app.get("/paths/{path_id}", response_model=PathDetail)
def get_path(path_id: str, conn: Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM paths WHERE id = ?", (path_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="path not found")
    return PathDetail(
        id=row["id"],
        status=row["status"],
        fill_rule=row["fill_rule"],
        metadata=json.loads(row["metadata"]),
        original={"d": row["d"], "contour_count": len(parse(row["d"]))},
        normalized=_load_normalized(conn, path_id),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
