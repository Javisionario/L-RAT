# -*- coding: utf-8 -*-

from __future__ import annotations

import math
from bisect import bisect_right
from statistics import median
from typing import List, Optional, Tuple

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsFeatureSink,
    QgsProcessingAlgorithm,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
)

try:
    import numpy as _np  # optional, used for Savitzky–Golay
except Exception:
    _np = None


# ----------------------------
# Constants / enums
# ----------------------------

SRC_REAL = 0
SRC_INTERP = 1
SRC_EXTRAP = 2
SRC_NODATA = 3

SLOPE_TYPE_TEXT = {
    SRC_REAL: "REAL",
    SRC_INTERP: "INTERP",
    SRC_EXTRAP: "EXTRAP",
    SRC_NODATA: "NODATA",
}

RES_NEAREST = 0
RES_BILINEAR = 1
RES_CUBIC = 2

SMOOTH_NONE = 0
SMOOTH_MOVAVG = 1
SMOOTH_MOVMED = 2
SMOOTH_SAVGOL = 3


# ----------------------------
# Helpers (private)
# ----------------------------

def _safe_isfinite(x: Optional[float]) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(float(x))


def _is_close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def _utm_crs_for_lonlat(lon: float, lat: float) -> QgsCoordinateReferenceSystem:
    """Returns a UTM CRS based on lon/lat (EPSG:326xx for N, EPSG:327xx for S)."""
    zone = int((lon + 180.0) // 6.0) + 1
    zone = max(1, min(60, zone))
    epsg = (32600 + zone) if lat >= 0 else (32700 + zone)
    return QgsCoordinateReferenceSystem(f"EPSG:{epsg}")


def _local_projected_crs_for_geometry(
    geom_in_layer_crs: QgsGeometry,
    layer_crs: QgsCoordinateReferenceSystem,
    context
) -> QgsCoordinateReferenceSystem:
    """Chooses an internal projected CRS (UTM) based on geometry centroid."""
    if geom_in_layer_crs.isEmpty():
        return QgsCoordinateReferenceSystem("EPSG:3857")

    centroid = geom_in_layer_crs.centroid()
    if centroid.isEmpty():
        return QgsCoordinateReferenceSystem("EPSG:3857")

    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    try:
        to_wgs = QgsCoordinateTransform(layer_crs, wgs84, context.transformContext())
        c = QgsGeometry(centroid)
        c.transform(to_wgs)
        pt = c.asPoint()
        lon, lat = float(pt.x()), float(pt.y())
        return _utm_crs_for_lonlat(lon, lat)
    except Exception:
        return QgsCoordinateReferenceSystem("EPSG:3857")


def _merge_lines_per_feature(geom: QgsGeometry) -> QgsGeometry:
    """Merge lines within a feature (linemerge-like)."""
    if geom is None or geom.isEmpty():
        return QgsGeometry()

    g = QgsGeometry(geom)
    if hasattr(g, "mergeLines"):
        try:
            mg = g.mergeLines()
            if mg and not mg.isEmpty():
                g = mg
        except Exception:
            pass
    return g


def _reverse_line_geometry(geom: QgsGeometry) -> QgsGeometry:
    """Reverse a (multi)line geometry order."""
    if geom is None or geom.isEmpty():
        return QgsGeometry()

    g = QgsGeometry(geom)
    if hasattr(g, "reverse"):
        try:
            g.reverse()
            return g
        except Exception:
            pass

    try:
        if g.isMultipart():
            parts = g.asMultiPolyline()
            if not parts:
                return g
            parts_rev = [list(reversed(pl)) for pl in parts]
            return QgsGeometry.fromMultiPolylineXY(parts_rev)
        else:
            pl = g.asPolyline()
            if pl:
                return QgsGeometry.fromPolylineXY(list(reversed(pl)))
    except Exception:
        return g

    return g


def _raster_geotransform(raster_layer) -> Optional[Tuple[float, float, float, float, float, float]]:
    dp = raster_layer.dataProvider()
    if hasattr(dp, "geoTransform"):
        try:
            gt = dp.geoTransform()
            if gt and len(gt) == 6:
                return tuple(float(x) for x in gt)
        except Exception:
            return None
    return None


def _pixel_center_from_rc(gt, col: int, row: int) -> QgsPointXY:
    x0, pxW, _, y0, _, pxH = gt
    x = x0 + (col + 0.5) * pxW
    y = y0 + (row + 0.5) * pxH
    return QgsPointXY(x, y)


def _rc_from_point(gt, x: float, y: float) -> Tuple[float, float]:
    x0, pxW, _, y0, _, pxH = gt
    col = (x - x0) / pxW
    row = (y - y0) / pxH
    return col, row


def _sample_nearest(dp, point: QgsPointXY, band: int, nodata: Optional[float]) -> Optional[float]:
    val, ok = dp.sample(point, band)
    if not ok or val is None:
        return None
    try:
        v = float(val)
        if not math.isfinite(v):
            return None
        if nodata is not None and _is_close(v, nodata, 1e-12):
            return None
        return v
    except Exception:
        return None


def _sample_bilinear(dp, gt, point: QgsPointXY, band: int, nodata: Optional[float]) -> Optional[float]:
    col_f, row_f = _rc_from_point(gt, point.x(), point.y())
    c0, r0 = math.floor(col_f), math.floor(row_f)
    c1, r1 = c0 + 1, r0 + 1
    tx = col_f - c0
    ty = row_f - r0

    p00 = _pixel_center_from_rc(gt, c0, r0)
    p10 = _pixel_center_from_rc(gt, c1, r0)
    p01 = _pixel_center_from_rc(gt, c0, r1)
    p11 = _pixel_center_from_rc(gt, c1, r1)

    z00 = _sample_nearest(dp, p00, band, nodata)
    z10 = _sample_nearest(dp, p10, band, nodata)
    z01 = _sample_nearest(dp, p01, band, nodata)
    z11 = _sample_nearest(dp, p11, band, nodata)

    if not all(_safe_isfinite(z) for z in (z00, z10, z01, z11)):
        return None

    z0 = (1 - tx) * z00 + tx * z10
    z1 = (1 - tx) * z01 + tx * z11
    return (1 - ty) * z0 + ty * z1


def _cubic_kernel_catmull_rom(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    a = -0.5
    t2 = t * t
    t3 = t2 * t
    return (
        (a * (-t3 + 2 * t2 - t) * p0)
        + ((a * (-t3 + t2) + (2 * t3 - 3 * t2 + 1)) * p1)
        + ((a * (t3 - 2 * t2 + t) + (-2 * t3 + 3 * t2)) * p2)
        + (a * (t3 - t2) * p3)
    )


def _sample_cubic(dp, gt, point: QgsPointXY, band: int, nodata: Optional[float]) -> Optional[float]:
    col_f, row_f = _rc_from_point(gt, point.x(), point.y())
    c = math.floor(col_f)
    r = math.floor(row_f)
    tx = col_f - c
    ty = row_f - r

    z = [[None] * 4 for _ in range(4)]
    for j in range(4):
        for i in range(4):
            cc = (c - 1) + i
            rr = (r - 1) + j
            pp = _pixel_center_from_rc(gt, cc, rr)
            z[j][i] = _sample_nearest(dp, pp, band, nodata)

    for row in z:
        if not all(_safe_isfinite(v) for v in row):
            return None

    inter_rows = []
    for j in range(4):
        p0, p1, p2, p3 = z[j][0], z[j][1], z[j][2], z[j][3]
        inter_rows.append(_cubic_kernel_catmull_rom(p0, p1, p2, p3, tx))

    return _cubic_kernel_catmull_rom(inter_rows[0], inter_rows[1], inter_rows[2], inter_rows[3], ty)


def _sample_dem(raster_layer, point_raster_crs: QgsPointXY, resampling: int) -> Optional[float]:
    dp = raster_layer.dataProvider()
    band = 1

    nodata = None
    try:
        if hasattr(dp, "sourceHasNoDataValue") and dp.sourceHasNoDataValue(band):
            nodata = float(dp.sourceNoDataValue(band))
    except Exception:
        nodata = None

    if resampling == RES_NEAREST:
        return _sample_nearest(dp, point_raster_crs, band, nodata)

    gt = _raster_geotransform(raster_layer)
    if gt is None:
        return _sample_nearest(dp, point_raster_crs, band, nodata)

    if resampling == RES_BILINEAR:
        return _sample_bilinear(dp, gt, point_raster_crs, band, nodata)

    if resampling == RES_CUBIC:
        v = _sample_cubic(dp, gt, point_raster_crs, band, nodata)
        if v is None:
            return _sample_bilinear(dp, gt, point_raster_crs, band, nodata)
        return v

    return _sample_nearest(dp, point_raster_crs, band, nodata)


def _auto_step_from_raster_in_meters(
    raster_layer,
    raster_crs: QgsCoordinateReferenceSystem,
    local_proj_crs: QgsCoordinateReferenceSystem,
    centroid_in_layer_crs: QgsPointXY,
    layer_crs: QgsCoordinateReferenceSystem,
    context
) -> float:
    """Returns a reasonable sampling step in meters using raster pixel size."""
    px = abs(float(raster_layer.rasterUnitsPerPixelX()))
    py = abs(float(raster_layer.rasterUnitsPerPixelY()))
    if px <= 0 or py <= 0:
        return 1.0

    try:
        to_raster = QgsCoordinateTransform(layer_crs, raster_crs, context.transformContext())
        pt_r = to_raster.transform(centroid_in_layer_crs)
    except Exception:
        pt_r = centroid_in_layer_crs

    p0 = QgsPointXY(pt_r.x(), pt_r.y())
    p1 = QgsPointXY(pt_r.x() + px, pt_r.y())
    p2 = QgsPointXY(pt_r.x(), pt_r.y() + py)

    try:
        r_to_loc = QgsCoordinateTransform(raster_crs, local_proj_crs, context.transformContext())
        q0 = r_to_loc.transform(p0)
        q1 = r_to_loc.transform(p1)
        q2 = r_to_loc.transform(p2)
        dx = math.hypot(q1.x() - q0.x(), q1.y() - q0.y())
        dy = math.hypot(q2.x() - q0.x(), q2.y() - q0.y())
        step = max(dx, dy)
        return max(step, 0.01)
    except Exception:
        return max(px, py, 0.01)


def _make_distances(length_m: float, step_m: float) -> List[float]:
    if length_m <= 0:
        return [0.0]
    step_m = max(step_m, 0.0001)

    dists = [0.0]
    d = step_m
    while d < (length_m - 1e-9):
        dists.append(d)
        d += step_m
    if not _is_close(dists[-1], length_m, 1e-7):
        dists.append(length_m)
    return dists


def _moving_average(values: List[Optional[float]], window: int) -> List[Optional[float]]:
    n = len(values)
    out = [None] * n
    if window <= 1:
        return values[:]
    half = window // 2

    for i in range(n):
        s = 0.0
        k = 0
        for j in range(max(0, i - half), min(n, i + half + 1)):
            v = values[j]
            if _safe_isfinite(v):
                s += float(v)
                k += 1
        out[i] = (s / k) if k > 0 else None
    return out


def _moving_median(values: List[Optional[float]], window: int) -> List[Optional[float]]:
    n = len(values)
    out = [None] * n
    if window <= 1:
        return values[:]
    half = window // 2

    for i in range(n):
        vv = []
        for j in range(max(0, i - half), min(n, i + half + 1)):
            v = values[j]
            if _safe_isfinite(v):
                vv.append(float(v))
        out[i] = median(vv) if vv else None
    return out


def _savgol(values: List[Optional[float]], window: int, poly_order: int) -> List[Optional[float]]:
    """Savitzky–Golay smoothing (0th derivative). Requires numpy; fallback to moving average."""
    if _np is None:
        return _moving_average(values, window)

    n = len(values)
    out = [None] * n
    if window < 3:
        return values[:]
    if window % 2 == 0:
        window += 1
    if poly_order < 1:
        poly_order = 1
    if poly_order >= window:
        poly_order = window - 1

    half = window // 2
    x = _np.arange(-half, half + 1, dtype=float)
    A = _np.vander(x, N=poly_order + 1, increasing=True)
    pinv = _np.linalg.pinv(A)
    w = pinv[0, :]

    i = 0
    while i < n:
        if not _safe_isfinite(values[i]):
            i += 1
            continue
        j = i
        while j < n and _safe_isfinite(values[j]):
            j += 1

        run_len = j - i
        if run_len < window:
            for k in range(i, j):
                out[k] = float(values[k])
        else:
            y = _np.array([float(values[k]) for k in range(i, j)], dtype=float)
            for k in range(run_len):
                left = max(0, k - half)
                right = min(run_len - 1, k + half)
                span = right - left + 1
                if span == window:
                    seg = y[k - half : k + half + 1]
                    out[i + k] = float(_np.dot(w, seg))
                else:
                    xx = _np.arange(left - k, right - k + 1, dtype=float)
                    yy = y[left : right + 1]
                    deg = min(poly_order, len(xx) - 1)
                    if deg < 1:
                        out[i + k] = float(y[k])
                    else:
                        coeff = _np.polyfit(xx, yy, deg)
                        out[i + k] = float(_np.polyval(coeff, 0.0))
        i = j

    return out


def _smooth_series(values: List[Optional[float]], method: int, window: int, poly_order: int) -> List[Optional[float]]:
    if method == SMOOTH_NONE:
        return values[:]
    if window < 1:
        window = 1
    if window % 2 == 0:
        window += 1
    if method == SMOOTH_MOVAVG:
        return _moving_average(values, window)
    if method == SMOOTH_MOVMED:
        return _moving_median(values, window)
    if method == SMOOTH_SAVGOL:
        return _savgol(values, window, poly_order)
    return values[:]


def _compute_slopes_between(dists: List[float], z: List[Optional[float]]) -> List[Optional[float]]:
    """Returns slopes for segments i..i+1 (length n-1). slope% = 100 * dz/dx."""
    n = len(dists)
    slopes = [None] * max(0, n - 1)
    for i in range(n - 1):
        z0 = z[i]
        z1 = z[i + 1]
        if not (_safe_isfinite(z0) and _safe_isfinite(z1)):
            slopes[i] = None
            continue
        dx = float(dists[i + 1] - dists[i])
        if dx <= 0:
            slopes[i] = None
            continue
        slopes[i] = 100.0 * (float(z1) - float(z0)) / dx
    return slopes


def _fill_missing_slopes(slopes: List[Optional[float]]) -> Tuple[List[Optional[float]], List[int]]:
    """Fill missing slopes to keep continuous segment layer (interp/extrap)."""
    n = len(slopes)
    filled = slopes[:]
    src = [SRC_REAL if _safe_isfinite(s) else SRC_NODATA for s in slopes]

    real_idx = [i for i, s in enumerate(slopes) if _safe_isfinite(s)]
    if not real_idx:
        return filled, [SRC_NODATA] * n

    i = 0
    while i < n:
        if _safe_isfinite(slopes[i]):
            i += 1
            continue

        a = i
        while i < n and not _safe_isfinite(slopes[i]):
            i += 1
        b = i - 1

        prev_i = a - 1
        while prev_i >= 0 and not _safe_isfinite(slopes[prev_i]):
            prev_i -= 1

        next_i = b + 1
        while next_i < n and not _safe_isfinite(slopes[next_i]):
            next_i += 1

        if prev_i >= 0 and next_i < n:
            s0 = float(slopes[prev_i])
            s1 = float(slopes[next_i])
            denom = (next_i - prev_i)
            for k in range(a, b + 1):
                t = (k - prev_i) / denom
                filled[k] = s0 + t * (s1 - s0)
                src[k] = SRC_INTERP
        elif prev_i >= 0:
            s0 = float(slopes[prev_i])
            for k in range(a, b + 1):
                filled[k] = s0
                src[k] = SRC_EXTRAP
        elif next_i < n:
            s1 = float(slopes[next_i])
            for k in range(a, b + 1):
                filled[k] = s1
                src[k] = SRC_EXTRAP
        else:
            for k in range(a, b + 1):
                filled[k] = None
                src[k] = SRC_NODATA

    return filled, src


def _extract_vertices_with_m(
    geom_layer_crs: QgsGeometry,
    layer_to_local: QgsCoordinateTransform
) -> Optional[Tuple[List[float], List[float]]]:
    """Returns (cum_dist_m, m_values) along vertices, distances in local projected meters."""
    if geom_layer_crs is None or geom_layer_crs.isEmpty():
        return None

    try:
        verts = [v for v in geom_layer_crs.vertices()]
    except Exception:
        return None
    if len(verts) < 2:
        return None

    m_vals = []
    xy_local = []
    for v in verts:
        if not hasattr(v, "m"):
            return None
        mv = v.m()
        if mv is None or (isinstance(mv, float) and not math.isfinite(mv)):
            return None
        m_vals.append(float(mv))
        try:
            p = layer_to_local.transform(QgsPointXY(v.x(), v.y()))
            xy_local.append((float(p.x()), float(p.y())))
        except Exception:
            return None

    cum = [0.0]
    for i in range(1, len(xy_local)):
        x0, y0 = xy_local[i - 1]
        x1, y1 = xy_local[i]
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))

    return cum, m_vals


def _interp_m_at_dist(cum_dist: List[float], m_vals: List[float], d: float) -> Optional[float]:
    """Linear interpolation of M at distance d."""
    if not cum_dist or not m_vals or len(cum_dist) != len(m_vals):
        return None
    if d <= cum_dist[0]:
        return float(m_vals[0])
    if d >= cum_dist[-1]:
        return float(m_vals[-1])

    idx = bisect_right(cum_dist, d) - 1
    idx = max(0, min(idx, len(cum_dist) - 2))
    d0, d1 = cum_dist[idx], cum_dist[idx + 1]
    if d1 <= d0:
        return float(m_vals[idx])
    t = (d - d0) / (d1 - d0)
    return float(m_vals[idx] + t * (m_vals[idx + 1] - m_vals[idx]))


def _format_pk_mmm(km_val: Optional[float]) -> Optional[str]:
    """Formats a kilometer value as PK+mmm (e.g., 12+345)."""
    if not _safe_isfinite(km_val):
        return None
    km_val = float(km_val)
    if km_val < 0:
        sign = -1
        km_abs = abs(km_val)
    else:
        sign = 1
        km_abs = km_val

    pk = int(math.floor(km_abs + 1e-12))
    mmm = int(round((km_abs - pk) * 1000.0))

    if mmm == 1000:
        pk += 1
        mmm = 0

    pk_signed = pk * sign
    return f"{pk_signed}+{mmm:03d}"


def _mark_advanced(param_def: QgsProcessingParameterDefinition) -> None:
    param_def.setFlags(param_def.flags() | QgsProcessingParameterDefinition.FlagAdvanced)


# ----------------------------
# Processing algorithm
# ----------------------------

class SlopeAndLongitudinalProfileAlgorithm(QgsProcessingAlgorithm):

    INPUT = "INPUT"
    DEM = "DEM"
    ID_FIELD = "ID_FIELD"
    STEP_M = "STEP_M"
    INVERT = "INVERT"

    # Advanced
    RESAMPLING = "RESAMPLING"
    SMOOTH_METHOD = "SMOOTH_METHOD"
    SMOOTH_WINDOW = "SMOOTH_WINDOW"
    SMOOTH_POLY = "SMOOTH_POLY"

    USE_M = "USE_M"
    M_UNITS = "M_UNITS"

    OUT_TABLE = "OUT_TABLE"
    OUT_SEGMENTS = "OUT_SEGMENTS"

    def name(self) -> str:
        return "slope_and_longitudinal_profile"

    def displayName(self) -> str:
        return "Slope and Longitudinal Profile"

    def group(self) -> str:
        return "Profile & Slope"

    def groupId(self) -> str:
        return "profile_slope"

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)
    
    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("slope-and-longitudinal-profile")

    def createInstance(self) -> QgsProcessingAlgorithm:
        return SlopeAndLongitudinalProfileAlgorithm()

    def initAlgorithm(self, config=None) -> None:
        # ----------------
        # BÁSICOS
        # ----------------
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr("Input line layer"),
                [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.DEM,
                self.tr("DEM (local raster or WCS)")
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.ID_FIELD,
                self.tr("Segment ID field"),
                parentLayerParameterName=self.INPUT,
                optional=True
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.STEP_M,
                self.tr("Sampling step (m). 0 = raster resolution"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
                optional=False,
                minValue=0.0
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INVERT,
                self.tr("Invert profile direction (origin at the opposite end)"),
                defaultValue=False
            )
        )

        # ----------------
        # AVANZADOS
        # ----------------
        p_res = QgsProcessingParameterEnum(
            self.RESAMPLING,
            self.tr("DEM sampling method"),
            options=[self.tr("Nearest"), self.tr("Bilinear"), self.tr("Cubic")],
            defaultValue=RES_CUBIC
        )
        _mark_advanced(p_res)
        self.addParameter(p_res)

        p_sm = QgsProcessingParameterEnum(
            self.SMOOTH_METHOD,
            self.tr("Profile smoothing"),
            options=[self.tr("None"), self.tr("Moving average"), self.tr("Moving median"), self.tr("Savitzky–Golay")],
            defaultValue=SMOOTH_SAVGOL
        )
        _mark_advanced(p_sm)
        self.addParameter(p_sm)

        p_win = QgsProcessingParameterNumber(
            self.SMOOTH_WINDOW,
            self.tr("Smoothing window (number of samples, odd)"),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=7,
            minValue=1
        )
        _mark_advanced(p_win)
        self.addParameter(p_win)

        p_poly = QgsProcessingParameterNumber(
            self.SMOOTH_POLY,
            self.tr("Polynomial order (Savitzky–Golay)"),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=2,
            minValue=1
        )
        _mark_advanced(p_poly)
        self.addParameter(p_poly)

        p_use_m = QgsProcessingParameterBoolean(
            self.USE_M,
            self.tr("Use M (if available) to add chainage (PK) field to the table"),
            defaultValue=False
        )
        _mark_advanced(p_use_m)
        self.addParameter(p_use_m)

        p_m_units = QgsProcessingParameterEnum(
            self.M_UNITS,
            self.tr("M units"),
            options=[self.tr("meters"), self.tr("kilometers")],
            defaultValue=0
        )
        _mark_advanced(p_m_units)
        self.addParameter(p_m_units)

        # Outputs
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_TABLE,
                self.tr("Profile table (no geometry)")
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_SEGMENTS,
                self.tr("Segmented profile (lines)")
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Invalid input line layer."))

        dem = self.parameterAsRasterLayer(parameters, self.DEM, context)
        if dem is None:
            raise QgsProcessingException(self.tr("Invalid DEM."))

        id_field = self.parameterAsString(parameters, self.ID_FIELD, context)
        step_m = float(self.parameterAsDouble(parameters, self.STEP_M, context))
        invert = bool(self.parameterAsBool(parameters, self.INVERT, context))

        # advanced
        resampling = int(self.parameterAsEnum(parameters, self.RESAMPLING, context))
        smooth_method = int(self.parameterAsEnum(parameters, self.SMOOTH_METHOD, context))
        window = int(self.parameterAsInt(parameters, self.SMOOTH_WINDOW, context))
        poly = int(self.parameterAsInt(parameters, self.SMOOTH_POLY, context))

        use_m = bool(self.parameterAsBool(parameters, self.USE_M, context))
        m_units = int(self.parameterAsEnum(parameters, self.M_UNITS, context))  # 0 meters, 1 km

        layer_crs = source.sourceCrs()
        raster_crs = dem.crs()

        # ---- Output fields: Table (NoGeometry)
        table_fields = QgsFields()
        table_fields.append(QgsField("ID_Segmento", QVariant.String))
        table_fields.append(QgsField("Dist_Origen_metros", QVariant.Double))
        table_fields.append(QgsField("Cota_RAW_metros", QVariant.Double))
        table_fields.append(QgsField("Cota_SUAV", QVariant.Double))
        table_fields.append(QgsField("SLOPE", QVariant.Double))
        table_fields.append(QgsField("SLOPE_TYPE", QVariant.String))

        if use_m:
            table_fields.append(QgsField("m_field_PK_KM", QVariant.Double))
            table_fields.append(QgsField("m_field_PK_MMM", QVariant.String))

        (table_sink, table_sink_id) = self.parameterAsSink(
            parameters,
            self.OUT_TABLE,
            context,
            table_fields,
            QgsWkbTypes.NoGeometry,
            layer_crs
        )
        if table_sink is None:
            raise QgsProcessingException(self.tr("Could not create the output table."))

        # ---- Output fields: Segments layer
        seg_fields = QgsFields()
        seg_fields.append(QgsField("ID_Segmento", QVariant.String))

        seg_fields.append(QgsField("D_Ini_metros", QVariant.Double))
        seg_fields.append(QgsField("D_Fin_metros", QVariant.Double))
        seg_fields.append(QgsField("D_Mid_metros", QVariant.Double))

        seg_fields.append(QgsField("Z_Ini_RAW_metros", QVariant.Double))
        seg_fields.append(QgsField("Z_Fin_RAW_metros", QVariant.Double))
        seg_fields.append(QgsField("Z_Mid_RAW_metros", QVariant.Double))

        seg_fields.append(QgsField("Z_Ini_SUAV_metros", QVariant.Double))
        seg_fields.append(QgsField("Z_Fin_SUAV_metros", QVariant.Double))
        seg_fields.append(QgsField("Z_Mid_SUAV_metros", QVariant.Double))

        seg_fields.append(QgsField("SLOPE", QVariant.Double))
        seg_fields.append(QgsField("SLOPE_TYPE", QVariant.String))

        seg_fields.append(QgsField("Long_tramo", QVariant.Double))

        (seg_sink, seg_sink_id) = self.parameterAsSink(
            parameters,
            self.OUT_SEGMENTS,
            context,
            seg_fields,
            QgsWkbTypes.LineString,
            layer_crs
        )
        if seg_sink is None:
            raise QgsProcessingException(self.tr("Could not create the segmented output layer."))

        total = source.featureCount()
        if total <= 0:
            total = 1

        for idx, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(100 * idx / total))

            geom0 = feat.geometry()
            if geom0 is None or geom0.isEmpty():
                continue

            geom = _merge_lines_per_feature(geom0)
            if geom is None or geom.isEmpty():
                continue

            if invert:
                geom = _reverse_line_geometry(geom)

            # resolve ID
            if id_field:
                try:
                    seg_id_val = str(feat[id_field])
                except Exception:
                    seg_id_val = str(feat.id())
            else:
                seg_id_val = str(feat.id())

            local_crs = _local_projected_crs_for_geometry(geom, layer_crs, context)

            to_local = QgsCoordinateTransform(layer_crs, local_crs, context.transformContext())
            from_local = QgsCoordinateTransform(local_crs, layer_crs, context.transformContext())
            local_to_raster = QgsCoordinateTransform(local_crs, raster_crs, context.transformContext())

            geom_local = QgsGeometry(geom)
            try:
                geom_local.transform(to_local)
            except Exception:
                continue

            length_m = geom_local.length()
            if length_m <= 0:
                continue

            # Determine step
            if step_m <= 0.0:
                try:
                    c = geom.centroid().asPoint()
                    centroid_layer = QgsPointXY(float(c.x()), float(c.y()))
                except Exception:
                    bb = geom.boundingBox()
                    centroid_layer = QgsPointXY(bb.center().x(), bb.center().y())

                step_eff = _auto_step_from_raster_in_meters(
                    dem, raster_crs, local_crs, centroid_layer, layer_crs, context
                )
            else:
                step_eff = max(step_m, 0.01)

            dists = _make_distances(length_m, step_eff)

            # Optional M mapping (for TABLE only)
            cum_dist_vertices = None
            m_vertices = None
            if use_m:
                m_data = _extract_vertices_with_m(geom, to_local)
                if m_data is not None:
                    cum_dist_vertices, m_vertices = m_data
                else:
                    cum_dist_vertices = None
                    m_vertices = None

            # Sample Z and keep point coords in layer CRS for segment geometry
            pts_layer: List[QgsPointXY] = []
            z_raw: List[Optional[float]] = []

            for d in dists:
                try:
                    gpt = geom_local.interpolate(d)
                    pt_local = gpt.asPoint()
                except Exception:
                    z_raw.append(None)
                    pts_layer.append(QgsPointXY())
                    continue

                try:
                    pt_r = local_to_raster.transform(QgsPointXY(pt_local.x(), pt_local.y()))
                    z = _sample_dem(dem, QgsPointXY(pt_r.x(), pt_r.y()), resampling)
                except Exception:
                    z = None

                z_raw.append(z)

                try:
                    pt_layer = from_local.transform(QgsPointXY(pt_local.x(), pt_local.y()))
                    pts_layer.append(QgsPointXY(pt_layer.x(), pt_layer.y()))
                except Exception:
                    pts_layer.append(QgsPointXY())

            z_suav = _smooth_series(z_raw, smooth_method, window, poly)
            z_for_slope = z_suav if smooth_method != SMOOTH_NONE else z_raw

            slopes = _compute_slopes_between(dists, z_for_slope)
            slopes_filled, slope_src = _fill_missing_slopes(slopes)

            # Compute PK fields (TABLE only)
            km_list: Optional[List[Optional[float]]] = None
            pk_mmm_list: Optional[List[Optional[str]]] = None

            if use_m and (cum_dist_vertices is not None) and (m_vertices is not None):
                km_list = []
                pk_mmm_list = []
                for d in dists:
                    m_i = _interp_m_at_dist(cum_dist_vertices, m_vertices, d)
                    if m_i is None or not math.isfinite(m_i):
                        km_list.append(None)
                        pk_mmm_list.append(None)
                        continue
                    km_val = (m_i / 1000.0) if (m_units == 0) else m_i
                    km_list.append(float(km_val))
                    pk_mmm_list.append(_format_pk_mmm(km_val))
            else:
                km_list = None
                pk_mmm_list = None

            # ---- TABLE rows (one per point)
            n = len(dists)
            for i in range(n):
                f = QgsFeature(table_fields)
                f["ID_Segmento"] = seg_id_val
                f["Dist_Origen_metros"] = float(dists[i])
                f["Cota_RAW_metros"] = float(z_raw[i]) if _safe_isfinite(z_raw[i]) else None
                f["Cota_SUAV"] = float(z_suav[i]) if _safe_isfinite(z_suav[i]) else None

                if i < n - 1:
                    sval = slopes_filled[i] if i < len(slopes_filled) else None
                    stype = slope_src[i] if i < len(slope_src) else SRC_NODATA
                    f["SLOPE"] = float(sval) if _safe_isfinite(sval) else None
                    f["SLOPE_TYPE"] = SLOPE_TYPE_TEXT.get(int(stype), "NODATA")
                else:
                    f["SLOPE"] = None
                    f["SLOPE_TYPE"] = "NODATA"

                if use_m:
                    if km_list is not None and pk_mmm_list is not None:
                        f["m_field_PK_KM"] = float(km_list[i]) if _safe_isfinite(km_list[i]) else None
                        f["m_field_PK_MMM"] = pk_mmm_list[i]
                    else:
                        f["m_field_PK_KM"] = None
                        f["m_field_PK_MMM"] = None

                table_sink.addFeature(f, QgsFeatureSink.FastInsert)

            # ---- SEGMENTS rows (one per segment)
            for i in range(n - 1):
                p0 = pts_layer[i]
                p1 = pts_layer[i + 1]
                if p0.isEmpty() or p1.isEmpty():
                    continue

                seg = QgsFeature(seg_fields)
                seg["ID_Segmento"] = seg_id_val

                d0 = float(dists[i])
                d1 = float(dists[i + 1])
                seg["D_Ini_metros"] = d0
                seg["D_Fin_metros"] = d1
                seg["D_Mid_metros"] = 0.5 * (d0 + d1)

                z0r = z_raw[i]
                z1r = z_raw[i + 1]
                z0s = z_suav[i]
                z1s = z_suav[i + 1]

                seg["Z_Ini_RAW_metros"] = float(z0r) if _safe_isfinite(z0r) else None
                seg["Z_Fin_RAW_metros"] = float(z1r) if _safe_isfinite(z1r) else None
                seg["Z_Mid_RAW_metros"] = (0.5 * (float(z0r) + float(z1r))) if (_safe_isfinite(z0r) and _safe_isfinite(z1r)) else None

                seg["Z_Ini_SUAV_metros"] = float(z0s) if _safe_isfinite(z0s) else None
                seg["Z_Fin_SUAV_metros"] = float(z1s) if _safe_isfinite(z1s) else None
                seg["Z_Mid_SUAV_metros"] = (0.5 * (float(z0s) + float(z1s))) if (_safe_isfinite(z0s) and _safe_isfinite(z1s)) else None

                sval = slopes_filled[i] if i < len(slopes_filled) else None
                stype = slope_src[i] if i < len(slope_src) else SRC_NODATA
                seg["SLOPE"] = float(sval) if _safe_isfinite(sval) else None
                seg["SLOPE_TYPE"] = SLOPE_TYPE_TEXT.get(int(stype), "NODATA")

                seg["Long_tramo"] = float(d1 - d0)

                seg.setGeometry(QgsGeometry.fromPolylineXY([p0, p1]))
                seg_sink.addFeature(seg, QgsFeatureSink.FastInsert)

        return {
            self.OUT_TABLE: table_sink_id,
            self.OUT_SEGMENTS: seg_sink_id
        }