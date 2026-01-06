# -*- coding: utf-8 -*-
from __future__ import annotations

from math import hypot, sqrt
from typing import List, Optional, Tuple

from collections import defaultdict

from qgis.core import (
    QgsGeometry,
    QgsWkbTypes,
    QgsPoint,
    QgsPointXY,
    QgsLineString,
    QgsMultiLineString,
    QgsProcessingParameterDefinition,
)


# ---------------------------------------------------------
# Processing / UI helpers
# ---------------------------------------------------------

def adv(param):
    """
    Mark a QgsProcessing parameter as 'Advanced'.
    Usage: self.addParameter(adv(QgsProcessingParameterX(...)))
    """
    param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return param


def safe_str(v, *, strip: bool = True) -> str:
    """Safe string conversion (optionally stripping whitespace)."""
    if v is None:
        return ""
    s = str(v)
    return s.strip() if strip else s


# ---------------------------------------------------------
# Geometry predicates / basic helpers
# ---------------------------------------------------------

def geom_is_line(geom: QgsGeometry) -> bool:
    return (
        geom is not None
        and (not geom.isEmpty())
        and QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.LineGeometry
    )


def geom_is_point(geom: QgsGeometry) -> bool:
    return (
        geom is not None
        and (not geom.isEmpty())
        and QgsWkbTypes.geometryType(geom.wkbType()) == QgsWkbTypes.PointGeometry
    )


def iter_parts(geom: QgsGeometry):
    """
    Iterate underlying geometry parts (QgsAbstractGeometry) in a flat way:
    - single: yields geom.constGet()
    - multi: yields each part from geom.constParts()
    """
    if geom is None or geom.isEmpty():
        return
    if not geom.isMultipart():
        yield geom.constGet()
        return
    for part in geom.constParts():
        yield part


def vertex_count(geom: QgsGeometry) -> int:
    if geom is None or geom.isEmpty():
        return 0
    n = 0
    for part in iter_parts(geom):
        for _ in part.vertices():
            n += 1
    return n


def has_any_m(geom: QgsGeometry) -> bool:
    """True if ANY vertex in the (multi)geometry has a measure (M)."""
    if geom is None or geom.isEmpty():
        return False
    for part in iter_parts(geom):
        for v in part.vertices():
            if v.isMeasure():
                return True
    return False


def collect_m_values_strict(geom: QgsGeometry) -> Optional[List[float]]:
    """
    Returns a flat list of M values for all vertices (iterating parts in order).
    If ANY vertex has no M, returns None.
    """
    if geom is None or geom.isEmpty():
        return None
    ms: List[float] = []
    for part in iter_parts(geom):
        for v in part.vertices():
            if not v.isMeasure():
                return None
            ms.append(float(v.m()))
    return ms


def geom_to_pointxy(geom: QgsGeometry) -> Optional[QgsPointXY]:
    """
    Extract a representative PointXY from a geometry.
    - For point geometries: asPoint()
    - Fallback: centroid (last resort)
    """
    if geom is None or geom.isEmpty():
        return None

    try:
        p = geom.asPoint()
        return QgsPointXY(p)
    except Exception:
        pass

    try:
        c = geom.centroid()
        if c and not c.isEmpty():
            p = c.asPoint()
            return QgsPointXY(p)
    except Exception:
        pass

    return None


# ---------------------------------------------------------
# M geometry rebuild helpers (preserve XY and Z)
# ---------------------------------------------------------

def make_point_with_m(x: float, y: float, z: Optional[float], m: float) -> QgsPoint:
    """
    Create QgsPoint with XY (+ optional Z) and set M.
    Keeps geometry 2D if z is None; does NOT force Z.
    """
    pt = QgsPoint(float(x), float(y)) if z is None else QgsPoint(float(x), float(y), float(z))
    pt.setM(float(m))
    return pt


def rebuild_geom_with_m(src_geom: QgsGeometry, m_values_by_vertex: list[float]) -> QgsGeometry:
    if src_geom is None or src_geom.isEmpty():
        return QgsGeometry()

    has_z = QgsWkbTypes.hasZ(src_geom.wkbType())
    idx = 0

    def part_to_coords(part):
        nonlocal idx
        coords = []
        for v in part.vertices():
            x = float(v.x()); y = float(v.y())
            m = float(m_values_by_vertex[idx]); idx += 1
            if has_z:
                z = float(v.z())
                coords.append(f"{x} {y} {z} {m}")
            else:
                coords.append(f"{x} {y} {m}")
        return ", ".join(coords)

    if not src_geom.isMultipart():
        part = src_geom.constGet()
        if has_z:
            wkt = f"LINESTRING ZM ({part_to_coords(part)})"
        else:
            wkt = f"LINESTRING M ({part_to_coords(part)})"
        return QgsGeometry.fromWkt(wkt)

    parts_wkt = []
    for part in src_geom.constParts():
        parts_wkt.append(f"({part_to_coords(part)})")

    if has_z:
        wkt = f"MULTILINESTRING ZM ({', '.join(parts_wkt)})"
    else:
        wkt = f"MULTILINESTRING M ({', '.join(parts_wkt)})"

    return QgsGeometry.fromWkt(wkt)

from collections import defaultdict
from math import hypot
from typing import List, Tuple, Optional

from qgis.core import QgsGeometry, QgsPointXY


def _dist(p: QgsPointXY, q: QgsPointXY) -> float:
    return hypot(float(q.x()) - float(p.x()), float(q.y()) - float(p.y()))


def _extract_parts_xy(g: QgsGeometry) -> List[List[QgsPointXY]]:
    """Devuelve puntos por parte, en el orden original."""
    if g is None or g.isEmpty():
        return []
    parts_pts: List[List[QgsPointXY]] = []
    if not g.isMultipart():
        part = g.constGet()
        pts = [QgsPointXY(v) for v in part.vertices()]
        if pts:
            parts_pts.append(pts)
        return parts_pts

    for part in g.constParts():
        pts = [QgsPointXY(v) for v in part.vertices()]
        if pts:
            parts_pts.append(pts)
    return parts_pts


def _snap_key(p: QgsPointXY, tol: float) -> Tuple[int, int]:
    if tol <= 0:
        # sin snap: usa escala grande (no recomendado)
        return (int(round(p.x() * 1e9)), int(round(p.y() * 1e9)))
    return (int(round(p.x() / tol)), int(round(p.y() / tol)))


def _order_parts_nearest(parts_pts: List[List[QgsPointXY]], tol: float) -> List[Tuple[int, bool, float]]:
    """
    Ordena partes para formar una cadena.
    Devuelve lista de tuplas: (idx_parte, reversed, gap)
      - reversed: si hay que recorrer esa parte al revés para conectar
      - gap: distancia entre fin de la parte anterior y el extremo elegido de la siguiente
    """
    n = len(parts_pts)
    if n <= 1:
        return [(0, False, 0.0)] if n == 1 else []

    # Heurística para elegir inicio: endpoints "terminales" (solo aparecen una vez)
    counts = defaultdict(int)
    for pts in parts_pts:
        counts[_snap_key(pts[0], tol)] += 1
        counts[_snap_key(pts[-1], tol)] += 1

    start_idx = 0
    start_rev = False
    found = False
    for i, pts in enumerate(parts_pts):
        k0 = counts[_snap_key(pts[0], tol)]
        k1 = counts[_snap_key(pts[-1], tol)]
        if k0 == 1 or k1 == 1:
            start_idx = i
            start_rev = (k1 == 1 and k0 != 1)  # si el terminal está en el final, invertimos
            found = True
            break
    if not found:
        start_idx = 0
        start_rev = False

    remaining = set(range(n))
    remaining.remove(start_idx)

    order: List[Tuple[int, bool, float]] = [(start_idx, start_rev, 0.0)]
    cur_end = parts_pts[start_idx][0] if start_rev else parts_pts[start_idx][-1]

    # Greedy: siguiente parte = la que tenga un extremo más cercano al extremo actual
    while remaining:
        best = None  # (dist, idx, rev)
        for j in remaining:
            pts = parts_pts[j]
            d0 = _dist(cur_end, pts[0])
            d1 = _dist(cur_end, pts[-1])
            if best is None or min(d0, d1) < best[0]:
                if d0 <= d1:
                    best = (d0, j, False)
                else:
                    best = (d1, j, True)

        gap, j, rev = float(best[0]), int(best[1]), bool(best[2])
        order.append((j, rev, gap))
        remaining.remove(j)

        # actualizar extremo actual
        cur_end = parts_pts[j][0] if rev else parts_pts[j][-1]

    return order


def continuous_along_distances(
    g: QgsGeometry,
    *,
    tol: float = 0.01,
    add_gaps: bool = True
) -> List[float]:
    """
    Devuelve distancias acumuladas (along) por vértice, en el orden ORIGINAL de iteración
    de vértices (partes en orden + vértices en orden dentro de cada parte).

    - Si es MultiLineString: ordena partes por endpoint más cercano para continuidad.
    - Si add_gaps=True: suma la distancia "salto" entre partes (si no tocan).
    """
    parts_pts = _extract_parts_xy(g)
    if not parts_pts:
        return []

    if len(parts_pts) == 1:
        pts = parts_pts[0]
        out = [0.0] * len(pts)
        acc = 0.0
        for i in range(1, len(pts)):
            acc += _dist(pts[i - 1], pts[i])
            out[i] = acc
        return out

    order = _order_parts_nearest(parts_pts, tol)

    along_by_part: List[Optional[List[float]]] = [None] * len(parts_pts)
    acc = 0.0

    for idx, rev, gap in order:
        if add_gaps:
            acc += float(gap)

        pts = parts_pts[idx]
        pts_oriented = list(reversed(pts)) if rev else pts

        along_oriented = [0.0] * len(pts_oriented)
        along_oriented[0] = acc

        for i in range(1, len(pts_oriented)):
            acc += _dist(pts_oriented[i - 1], pts_oriented[i])
            along_oriented[i] = acc

        # volver al orden original de vértices de ESA parte
        along_by_part[idx] = list(reversed(along_oriented)) if rev else along_oriented

    # aplanar en el orden original de partes
    flat: List[float] = []
    for vals in along_by_part:
        if vals:
            flat.extend(vals)
    return flat


# ---------------------------------------------------------
# Projection / locate helpers (calibration)
# ---------------------------------------------------------

def iter_linestring_geoms(g: QgsGeometry) -> List[QgsGeometry]:
    """
    Return a list of single LineString geometries (no multi) for robust operations.
    """
    if g is None or g.isEmpty():
        return []
    if not g.isMultipart():
        return [g]

    out: List[QgsGeometry] = []
    try:
        for part in g.constParts():
            out.append(QgsGeometry(part.clone()))
        return out
    except Exception:
        # fallback: asMultiPolyline
        try:
            mpls = g.asMultiPolyline()
            for pl in mpls:
                out.append(QgsGeometry.fromPolylineXY([QgsPointXY(p) for p in pl]))
        except Exception:
            pass
    return out


def locate_along_best_part(multiline_geom: QgsGeometry, pxy: QgsPointXY) -> Optional[Tuple[float, float]]:
    """
    For a (multi)line geometry, returns (dist_along, dist_axis) for the best-matching part:
      - dist_along: distance along that part (units of geom CRS)
      - dist_axis: perpendicular distance point->part (units of geom CRS)

    Uses closestSegmentWithContext (fast) and measures along using lineLocatePoint.
    """
    if multiline_geom is None or multiline_geom.isEmpty() or pxy is None:
        return None

    best_axis2: Optional[float] = None
    best_along: Optional[float] = None

    for part_g in iter_linestring_geoms(multiline_geom):
        if part_g is None or part_g.isEmpty():
            continue

        # closestSegmentWithContext returns (dist2, minDistPoint, afterVertex, leftOf)
        try:
            dist2, closest_pt, _, _ = part_g.closestSegmentWithContext(pxy)
        except Exception:
            continue

        if best_axis2 is None or float(dist2) < best_axis2:
            # Distance along line: locate on closest projected point (more stable than original point)
            try:
                along = float(part_g.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(closest_pt))))
            except Exception:
                continue
            best_axis2 = float(dist2)
            best_along = along

    if best_axis2 is None or best_along is None:
        return None

    return float(best_along), float(sqrt(best_axis2))


def closest_m_on_geom_fast(line_geom: QgsGeometry, pxy: QgsPointXY) -> Optional[Tuple[float, float]]:
    """
    Returns (dist_axis, m_interp) for the closest point on the (multi)line.
    Requires that the closest segment's endpoints BOTH have M.

    This is a faster replacement for scanning all segments:
    - finds closest segment via closestSegmentWithContext()
    - interpolates M only on that segment
    """
    if line_geom is None or line_geom.isEmpty() or pxy is None:
        return None

    best_dist2: Optional[float] = None
    best_m: Optional[float] = None

    for part in iter_parts(line_geom):
        # Wrap part as QgsGeometry for closestSegmentWithContext (API lives on QgsGeometry)
        try:
            part_g = QgsGeometry(part.clone())
        except Exception:
            continue

        if part_g.isEmpty():
            continue

        try:
            dist2, closest_pt, after_vertex, _ = part_g.closestSegmentWithContext(pxy)
        except Exception:
            continue

        # after_vertex indexes the vertex AFTER the closest segment
        try:
            av = int(after_vertex)
        except Exception:
            continue

        # Need the segment endpoints: (av-1, av)
        if av <= 0:
            continue

        # Build a vertex list ONCE for this part
        verts = list(part.vertices())
        if av >= len(verts):
            continue

        v0 = verts[av - 1]
        v1 = verts[av]

        if not (v0.isMeasure() and v1.isMeasure()):
            continue

        x0, y0, m0 = float(v0.x()), float(v0.y()), float(v0.m())
        x1, y1, m1 = float(v1.x()), float(v1.y()), float(v1.m())

        dx = x1 - x0
        dy = y1 - y0
        seg_len2 = dx * dx + dy * dy
        if seg_len2 <= 0.0:
            continue

        # t of projection of closest point onto segment param
        cx, cy = float(closest_pt.x()), float(closest_pt.y())
        t = ((cx - x0) * dx + (cy - y0) * dy) / seg_len2
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0

        m_interp = m0 + t * (m1 - m0)

        if best_dist2 is None or float(dist2) < best_dist2:
            best_dist2 = float(dist2)
            best_m = float(m_interp)

    if best_dist2 is None or best_m is None:
        return None

    return float(sqrt(best_dist2)), float(best_m)


__all__ = [
    # Processing
    "adv",
    "safe_str",
    # Predicates / basic
    "geom_is_line",
    "geom_is_point",
    "iter_parts",
    "vertex_count",
    "has_any_m",
    "collect_m_values_strict",
    "geom_to_pointxy",
    # Rebuild
    "make_point_with_m",
    "rebuild_geom_with_m",
    # Locate/projection
    "iter_linestring_geoms",
    "locate_along_best_part",
    "closest_m_on_geom_fast",
    "continuous_along_distances",
]