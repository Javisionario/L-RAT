# -*- coding: utf-8 -*-
from __future__ import annotations

from math import hypot
from typing import Optional, Tuple, List, Iterable

from qgis.core import (
    QgsGeometry,
    QgsWkbTypes,
    QgsPoint,
    QgsLineString,
    QgsMultiLineString,
    QgsDistanceArea,
    QgsCoordinateReferenceSystem,
    QgsProject,
)


# ---------------------------------------------------------
# PK helpers
# ---------------------------------------------------------

def pk_to_km(pk_text) -> float:
    """
    Convierte un PK:
      - '12+345' -> 12.345 (km)
      - '12.345' -> 12.345 (km)
      - int/float -> float (km)
    """
    if pk_text is None:
        raise ValueError("PK vacío")

    if isinstance(pk_text, (int, float)):
        return float(pk_text)

    s = str(pk_text).strip().replace(" ", "").replace(",", ".")
    if not s:
        raise ValueError("PK vacío")

    if "+" in s:
        km, m = s.split("+", 1)
        if km == "":
            raise ValueError(f"PK inválido: {pk_text!r}")
        if m == "":
            m = "0"
        return float(km) + float(m) / 1000.0

    return float(s)


def format_pk(km_float: float) -> str:
    """Devuelve 'km+mmm' a partir de km (float). Corrige el caso m=1000."""
    km = int(km_float)
    m = int(round((km_float - km) * 1000.0))

    if m >= 1000:
        km += 1
        m -= 1000
    elif m < 0:
        km -= 1
        m += 1000

    return f"{km}+{m:03d}"


def normalize_pk_range(pk_a: float, pk_b: float) -> Tuple[float, float]:
    """Devuelve (min, max) para permitir que PK ini > PK fin en el input."""
    return (pk_a, pk_b) if pk_a <= pk_b else (pk_b, pk_a)


def pk_distance_km(pk_ini_km: float, pk_fin_km: float) -> float:
    """Distancia por PK en km (valor absoluto)."""
    return abs(pk_fin_km - pk_ini_km)


# ---------------------------------------------------------
# Distance / length helpers
# ---------------------------------------------------------

def geom_length_km(
    geom: QgsGeometry,
    crs: QgsCoordinateReferenceSystem,
    project: QgsProject,
) -> float:
    """
    Longitud de una geometría en km usando QgsDistanceArea.
    - En CRS proyectados: mide en el CRS (m) y pasa a km.
    - En CRS geográficos: usa el elipsoide del proyecto (medida geodésica).
    """
    if geom is None or geom.isEmpty():
        return 0.0

    da = QgsDistanceArea()
    da.setSourceCrs(crs, project.transformContext())

    ellps = project.ellipsoid()
    if ellps:
        da.setEllipsoid(ellps)

    return da.measureLength(geom) / 1000.0


def round3(x: float) -> float:
    """Redondeo numérico a 3 decimales."""
    return round(float(x), 3)


# ---------------------------------------------------------
# M helpers (with units + tolerance)
# ---------------------------------------------------------

def _m_to_km(m_value: float, m_unit: str) -> float:
    """
    Convierte el valor M del vértice a kilómetros.
    m_unit:
      - "m": M está en metros -> km = m/1000
      - "km": M ya está en kilómetros -> km = m
    """
    if m_unit == "km":
        return float(m_value)
    return float(m_value) / 1000.0


def m_range_km(geom: QgsGeometry, *, m_unit: str = "m") -> Tuple[Optional[float], Optional[float]]:
    """
    Devuelve (min_m_km, max_m_km) recorriendo vértices.
    - Resultado SIEMPRE en km.
    - m_unit indica cómo interpretar el M del vértice ("m" o "km").
    """
    if geom is None or geom.isEmpty():
        return None, None

    mn = None
    mx = None

    parts = [geom.constGet()] if not geom.isMultipart() else list(geom.constParts())
    for part in parts:
        for v in part.vertices():
            if not v.isMeasure():
                continue
            m_km = _m_to_km(v.m(), m_unit)
            if mn is None or m_km < mn:
                mn = m_km
            if mx is None or m_km > mx:
                mx = m_km

    return mn, mx


def _cum_lengths_xy(verts: List[QgsPoint]) -> List[float]:
    """Longitud acumulada XY a lo largo de una lista de vértices."""
    cum = [0.0]
    for i in range(1, len(verts)):
        p0, p1 = verts[i - 1], verts[i]
        cum.append(cum[-1] + hypot(p1.x() - p0.x(), p1.y() - p0.y()))
    return cum


def find_distance_for_m_km(
    geom: QgsGeometry,
    target_m_km: float,
    *,
    m_unit: str = "m",
    clamp: bool = False,
    tolerance_km: float = 0.0,
) -> Optional[float]:
    """
    Convierte un PK objetivo (en km) a distancia acumulada XY desde el inicio.
    - target_m_km SIEMPRE está en km.
    - m_unit indica unidades del M almacenado en la geometría ("m" o "km").
    - clamp=True: si target está fuera del rango global, devuelve 0 o longitud total.
    - clamp=False: devuelve None si está fuera de rango o no se puede calcular.
    - tolerance_km: tolerancia en km para evitar fallos por redondeos (snap a extremos).
    """
    if geom is None or geom.isEmpty():
        return None

    mn, mx = m_range_km(geom, m_unit=m_unit)
    if mn is None or mx is None:
        return None

    if target_m_km < mn - tolerance_km:
        return 0.0 if clamp else None
    if target_m_km > mx + tolerance_km and not clamp:
        return None

    parts = [geom.constGet()] if not geom.isMultipart() else list(geom.constParts())
    last_len = None

    for part in parts:
        verts = list(part.vertices())
        if len(verts) < 2:
            continue

        cum = _cum_lengths_xy(verts)
        last_len = cum[-1]

        if clamp and target_m_km > mx + tolerance_km:
            return cum[-1]

        for i in range(len(verts) - 1):
            v0 = verts[i]
            v1 = verts[i + 1]
            if not v0.isMeasure() or not v1.isMeasure():
                continue

            m1 = _m_to_km(v0.m(), m_unit)
            m2 = _m_to_km(v1.m(), m_unit)

            lo = min(m1, m2) - tolerance_km
            hi = max(m1, m2) + tolerance_km

            if not (lo <= target_m_km <= hi):
                continue

            if abs(target_m_km - m1) <= tolerance_km:
                return cum[i]
            if abs(target_m_km - m2) <= tolerance_km:
                return cum[i + 1]

            seg_len = cum[i + 1] - cum[i]
            if abs(m2 - m1) < 1e-15:
                return cum[i]

            t = (target_m_km - m1) / (m2 - m1)
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            return cum[i] + t * seg_len

    if clamp and target_m_km > mx + tolerance_km and last_len is not None:
        return last_len

    return None


def find_distance_for_m(geom: QgsGeometry, target_m_km: float) -> float:
    """Compatibilidad: devuelve float o lanza ValueError (asume M en metros, sin tolerancia)."""
    d = find_distance_for_m_km(geom, target_m_km, m_unit="m", clamp=False, tolerance_km=0.0)
    if d is None:
        raise ValueError("PK fuera de rango M o geometría no calibrada.")
    return d


# ---------------------------------------------------------
# Substring by distance (XY)
# ---------------------------------------------------------

def _interp_point(p0: QgsPoint, p1: QgsPoint, t: float, wantZ: bool, wantM: bool) -> QgsPoint:
    """Interpola un punto entre p0 y p1 con t en [0, 1], preservando Z/M si aplica."""
    x = p0.x() + t * (p1.x() - p0.x())
    y = p0.y() + t * (p1.y() - p0.y())

    z = None
    if wantZ:
        if p0.is3D() and p1.is3D():
            z = p0.z() + t * (p1.z() - p0.z())
        elif p0.is3D():
            z = p0.z()
        elif p1.is3D():
            z = p1.z()
        else:
            z = 0.0

    m = None
    if wantM:
        m0 = p0.m() if p0.isMeasure() else 0.0
        m1 = p1.m() if p1.isMeasure() else m0
        m = m0 + t * (m1 - m0)

    if wantZ and wantM:
        return QgsPoint(x, y, z, m)
    if wantZ:
        return QgsPoint(x, y, z)
    if wantM:
        pt = QgsPoint(x, y)
        pt.setM(m)
        return pt
    return QgsPoint(x, y)


def _substring_part_by_distance(
    verts: List[QgsPoint],
    d0: float,
    d1: float,
    wantZ: bool,
    wantM: bool,
    tol: float = 1e-9,
) -> Optional[QgsLineString]:
    """Extrae un subsegmento entre distancias XY acumuladas d0 y d1."""
    if len(verts) < 2:
        return None
    if d1 < d0:
        d0, d1 = d1, d0
    if d1 - d0 <= tol:
        return None

    cum = _cum_lengths_xy(verts)
    out_pts: List[QgsPoint] = []

    for i in range(len(verts) - 1):
        seg_start = cum[i]
        seg_end = cum[i + 1]
        seg_len = seg_end - seg_start
        if seg_len <= tol:
            continue

        ov_start = max(d0, seg_start)
        ov_end = min(d1, seg_end)
        if ov_end - ov_start <= tol:
            continue

        t0 = 0.0 if abs(ov_start - seg_start) <= tol else (ov_start - seg_start) / seg_len
        t1 = 1.0 if abs(ov_end - seg_end) <= tol else (ov_end - seg_start) / seg_len

        pA = _interp_point(verts[i], verts[i + 1], t0, wantZ, wantM)
        pB = _interp_point(verts[i], verts[i + 1], t1, wantZ, wantM)

        if not out_pts:
            out_pts.append(pA)
        else:
            lp = out_pts[-1]
            if hypot(lp.x() - pA.x(), lp.y() - pA.y()) > tol:
                out_pts.append(pA)

        out_pts.append(pB)

    if len(out_pts) < 2:
        return None

    return QgsLineString(out_pts)


def geom_substring_by_distance(geom: QgsGeometry, dist0: float, dist1: float, wkbtype: int) -> Optional[QgsGeometry]:
    """
    Extrae un subsegmento entre distancias XY acumuladas dist0 y dist1.
    NOTA: esto NO recorta por M directamente; recorta por distancia geométrica (XY).
    """
    if geom is None or geom.isEmpty():
        return None

    wantZ = QgsWkbTypes.hasZ(wkbtype)
    wantM = QgsWkbTypes.hasM(wkbtype)

    if geom.isMultipart():
        pieces = []
        for part in geom.constParts():
            verts = list(part.vertices())
            ls = _substring_part_by_distance(verts, dist0, dist1, wantZ, wantM)
            if ls and not ls.isEmpty():
                pieces.append(ls)

        if not pieces:
            return None
        if len(pieces) == 1:
            return QgsGeometry(pieces[0])

        ml = QgsMultiLineString()
        for ls in pieces:
            ml.addGeometry(ls)
        return QgsGeometry(ml)

    verts = list(geom.constGet().vertices())
    ls = _substring_part_by_distance(verts, dist0, dist1, wantZ, wantM)
    return None if not ls or ls.isEmpty() else QgsGeometry(ls)


def geom_substring(geom: QgsGeometry, d0: float, d1: float, wkbtype: int) -> Optional[QgsGeometry]:
    """Alias por compatibilidad."""
    return geom_substring_by_distance(geom, d0, d1, wkbtype)


# ---------------------------------------------------------
# Segment extraction over multiple geometries (handles gaps)
# ---------------------------------------------------------

def extract_segment_from_route_geoms(
    geoms: List[QgsGeometry],
    pk_ini_km: float,
    pk_fin_km: float,
    wkbtype: int,
    *,
    m_unit: str = "m",
    tolerance_km: float = 0.0,
) -> Tuple[Optional[QgsGeometry], Optional[float], Optional[float], int, bool]:
    """
    Extrae un segmento por PK sobre una lista de geometrías (todas de la misma ROUTE_ID),
    devolviendo UNA geometría (Line o MultiLine) con todos los trozos válidos.

    Devuelve:
      (geom_out, pk_ini_real, pk_fin_real, n_piezas, clipped)

    - pk_ini_real/pk_fin_real SIEMPRE en km.
    - clipped=True si se recortó al rango global disponible.
    - n_piezas>1 indica discontinuidades/gaps (útil para warnings).
    """
    if not geoms:
        return None, None, None, 0, False

    # Rango global disponible (por M) en la ruta
    mins: List[float] = []
    maxs: List[float] = []
    for g in geoms:
        if g is None or g.isEmpty():
            continue
        mn, mx = m_range_km(g, m_unit=m_unit)
        if mn is not None and mx is not None:
            mins.append(mn)
            maxs.append(mx)

    if not mins:
        return None, None, None, 0, False

    global_mn = min(mins)
    global_mx = max(maxs)

    pk_ini, pk_fin = normalize_pk_range(pk_ini_km, pk_fin_km)
    clipped = False

    if pk_ini < global_mn:
        pk_ini = global_mn
        clipped = True
    if pk_fin > global_mx:
        pk_fin = global_mx
        clipped = True
    if pk_fin <= pk_ini:
        return None, pk_ini, pk_fin, 0, clipped

    pieces: List[QgsLineString] = []

    def _append_geom(outg: QgsGeometry):
        if outg.isMultipart():
            for part in outg.constParts():
                if not part.isEmpty():
                    pieces.append(part.clone())
        else:
            part = outg.constGet()
            if part and not part.isEmpty():
                pieces.append(part.clone())

    # Extraer intersección del rango con cada geom individual
    for g in geoms:
        if g is None or g.isEmpty():
            continue

        mn, mx = m_range_km(g, m_unit=m_unit)
        if mn is None or mx is None:
            continue

        a = max(pk_ini, mn)
        b = min(pk_fin, mx)
        if b <= a:
            continue

        d0 = find_distance_for_m_km(g, a, m_unit=m_unit, clamp=True, tolerance_km=tolerance_km)
        d1 = find_distance_for_m_km(g, b, m_unit=m_unit, clamp=True, tolerance_km=tolerance_km)
        if d0 is None or d1 is None:
            continue

        sub = geom_substring(g, d0, d1, wkbtype)
        if sub and not sub.isEmpty():
            _append_geom(sub)

    if not pieces:
        return None, pk_ini, pk_fin, 0, clipped

    if len(pieces) == 1:
        return QgsGeometry(pieces[0]), pk_ini, pk_fin, 1, clipped

    ml = QgsMultiLineString()
    for ls in pieces:
        ml.addGeometry(ls)
    return QgsGeometry(ml), pk_ini, pk_fin, len(pieces), clipped


# ---------------------------------------------------------
# NEW: helpers for gaps / range / issues (shared pattern)
# ---------------------------------------------------------

def global_m_range_km(geoms: List[QgsGeometry], *, m_unit: str = "m") -> Tuple[Optional[float], Optional[float]]:
    """Rango global (min,max) de M en km para una lista de geometrías."""
    mins: List[float] = []
    maxs: List[float] = []
    for g in geoms:
        if g is None or g.isEmpty():
            continue
        mn, mx = m_range_km(g, m_unit=m_unit)
        if mn is None or mx is None:
            continue
        mins.append(mn)
        maxs.append(mx)
    return (min(mins), max(maxs)) if mins and maxs else (None, None)


def is_pk_covered_by_any_geom(geoms: List[QgsGeometry], pk_km: float, *, m_unit: str = "m") -> bool:
    """True si pk_km cae dentro de [mn,mx] de alguna geometría individual."""
    for g in geoms:
        mn, mx = m_range_km(g, m_unit=m_unit)
        if mn is None or mx is None:
            continue
        if mn <= pk_km <= mx:
            return True
    return False


def nearest_available_m_km(geoms: List[QgsGeometry], target_km: float, *, m_unit: str = "m") -> Optional[float]:
    """Devuelve el extremo (mn/mx) más cercano al target_km, en km."""
    best = None  # (absdiff, m_km)
    for g in geoms:
        mn, mx = m_range_km(g, m_unit=m_unit)
        if mn is None or mx is None:
            continue
        for cand in (mn, mx):
            d = abs(target_km - cand)
            if best is None or d < best[0]:
                best = (d, cand)
    return None if best is None else best[1]


def codes_to_str(codes: Iterable[str]) -> str:
    """Serializa códigos (warnings/criticals) como 'A;B;C'."""
    return ";".join([c for c in codes if c])
