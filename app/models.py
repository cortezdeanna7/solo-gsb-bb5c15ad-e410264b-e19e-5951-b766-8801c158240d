"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PathCreate(BaseModel):
    d: str = Field(..., min_length=1, description="SVG path data (the 'd' attribute)")
    fill_rule: Literal["nonzero", "evenodd"] = "nonzero"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PathCreated(BaseModel):
    id: str
    status: str
    fill_rule: str
    metadata: dict[str, Any]
    contour_count: int
    created_at: datetime


class NormalizeRequest(BaseModel):
    outer_orientation: Literal["ccw", "cw"] = Field(
        "ccw",
        description="Target winding of outer rings as seen on screen; "
        "holes receive the opposite winding",
    )
    flatten_tolerance: float = Field(
        0.1, gt=0, le=100,
        description="Max deviation of flattened curves from the true curve",
    )
    precision: int = Field(
        3, ge=0, le=10, description="Decimal digits in normalized coordinates",
    )


class ContourOut(BaseModel):
    index: int = Field(..., description="Original subpath index (0-based)")
    parent_index: int | None
    depth: int = Field(..., description="Containment depth (0 = top level)")
    role: str
    orientation: str
    signed_area: float
    point_count: int
    d: str
    excluded: bool


class WarningOut(BaseModel):
    code: str
    message: str
    contour_index: int | None


class NormalizedOut(BaseModel):
    d: str
    outer_orientation: str
    contours: list[ContourOut]
    warnings: list[WarningOut]
    bounds: tuple[float, float, float, float] | None = None


class PathDetail(BaseModel):
    id: str
    status: str
    fill_rule: str
    metadata: dict[str, Any]
    original: dict[str, Any]
    normalized: NormalizedOut | None
    created_at: datetime
    updated_at: datetime


class PathSummary(BaseModel):
    id: str
    status: str
    fill_rule: str
    contour_count: int
    created_at: datetime
