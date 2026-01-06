# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from math import hypot

from qgis.PyQt.QtCore import QVariant, QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsGeometry,
    QgsPointXY,
    QgsFeatureSink,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    Qgis,
)

from .calib_utils import (
    adv,
    has_any_m,
    geom_is_line,
    rebuild_geom_with_m,
    continuous_along_distances,
)

# -------------------------
# Local CRS helpers (specific to this algorithm)
# -------------------------

MODE_AUTO = 0
MODE_PLANAR = 1
MODE_GEODESIC = 2


def _is_geographic(crs: QgsCoordinateReferenceSystem) -> bool:
    try:
        return crs.isGeographic()
    except Exception:
        return False


def _utm_crs_for_lonlat(lon: float, lat: float) -> QgsCoordinateReferenceSystem:
    zone = int((lon + 180.0) // 6.0) + 1
    zone = max(1, min(60, zone))
    epsg = (32600 + zone) if lat >= 0 else (32700 + zone)
    return QgsCoordinateReferenceSystem(f"EPSG:{epsg}")


def _local_projected_crs_for_geom(
    geom_in_layer_crs: QgsGeometry,
    layer_crs: QgsCoordinateReferenceSystem,
    context,
) -> QgsCoordinateReferenceSystem:
    """Pick a local UTM CRS based on geometry centroid (fallback EPSG:3857)."""
    if geom_in_layer_crs is None or geom_in_layer_crs.isEmpty():
        return QgsCoordinateReferenceSystem("EPSG:3857")

    centroid = geom_in_layer_crs.centroid()
    if centroid is None or centroid.isEmpty():
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


# -------------------------
# Multipart ordering helpers
# -------------------------

def _dist_xy(a: QgsPointXY, b: QgsPointXY) -> float:
    return hypot(float(b.x()) - float(a.x()), float(b.y()) - float(a.y()))


def _snap_key(p: QgsPointXY, tol: float) -> Tuple[int, int]:
    if tol <= 0:
        return (int(round(p.x() * 1e9)), int(round(p.y() * 1e9)))
    return (int(round(p.x() / tol)), int(round(p.y() / tol)))


def _point_to_work_xy(pxy: QgsPointXY, to_work: Optional[QgsCoordinateTransform]) -> QgsPointXY:
    if to_work is None:
        return pxy
    try:
        g = QgsGeometry.fromPointXY(pxy)
        g.transform(to_work)
        return QgsPointXY(g.asPoint())
    except Exception:
        return pxy


def _extract_parts_points(geom: QgsGeometry) -> List[List]:
    """List of parts, each part is list of QgsPoint (keeps Z if present)."""
    parts: List[List] = []
    if geom is None or geom.isEmpty():
        return parts
    if not geom.isMultipart():
        part = geom.constGet()
        pts = [v for v in part.vertices()]
        if pts:
            parts.append(pts)
        return parts

    for part in geom.constParts():
        pts = [v for v in part.vertices()]
        if pts:
            parts.append(pts)
    return parts


def _order_parts_nearest(
    parts_pts: List[List],
    to_work: Optional[QgsCoordinateTransform],
    tol: float,
) -> List[Tuple[int, bool]]:
    """
    Returns ordered list: (part_index, reversed)
    Chooses next part by nearest endpoint to current chain end.
    """
    n = len(parts_pts)
    if n <= 1:
        return [(0, False)] if n == 1 else []

    # endpoint counts to find terminal endpoints (appear once)
    counts: Dict[Tuple[int, int], int] = {}
    endpoints_work: List[Tuple[QgsPointXY, QgsPointXY]] = []

    for pts in parts_pts:
        p0 = QgsPointXY(float(pts[0].x()), float(pts[0].y()))
        p1 = QgsPointXY(float(pts[-1].x()), float(pts[-1].y()))
        w0 = _point_to_work_xy(p0, to_work)
        w1 = _point_to_work_xy(p1, to_work)
        endpoints_work.append((w0, w1))

        k0 = _snap_key(w0, tol)
        k1 = _snap_key(w1, tol)
        counts[k0] = counts.get(k0, 0) + 1
        counts[k1] = counts.get(k1, 0) + 1

    # pick a start part: prefer a terminal endpoint
    start_idx = 0
    start_rev = False
    found = False
    for i, (w0, w1) in enumerate(endpoints_work):
        c0 = counts.get(_snap_key(w0, tol), 0)
        c1 = counts.get(_snap_key(w1, tol), 0)
        if c0 == 1 or c1 == 1:
            start_idx = i
            # if the terminal endpoint is w1 (end), reverse so chain starts there
            start_rev = (c1 == 1 and c0 != 1)
            found = True
            break
    if not found:
        start_idx = 0
        start_rev = False

    remaining = set(range(n))
    remaining.remove(start_idx)

    order: List[Tuple[int, bool]] = [(start_idx, start_rev)]
    cur_end = endpoints_work[start_idx][0] if start_rev else endpoints_work[start_idx][1]

    while remaining:
        best_dist = None
        best_j = None
        best_rev = False

        for j in remaining:
            w0, w1 = endpoints_work[j]
            d0 = _dist_xy(cur_end, w0)
            d1 = _dist_xy(cur_end, w1)
            if best_dist is None or min(d0, d1) < best_dist:
                if d0 <= d1:
                    best_dist = d0
                    best_j = j
                    best_rev = False
                else:
                    best_dist = d1
                    best_j = j
                    best_rev = True

        order.append((int(best_j), bool(best_rev)))
        remaining.remove(best_j)

        w0, w1 = endpoints_work[best_j]
        cur_end = w0 if best_rev else w1

    return order


def _geom_from_ordered_parts(original_geom: QgsGeometry, ordered_parts: List[List]) -> QgsGeometry:
    """
    Build a (Multi)LineString geometry (WITHOUT M) from ordered parts.
    Keeps Z if present.
    """
    has_z = QgsWkbTypes.hasZ(original_geom.wkbType())
    is_multi = len(ordered_parts) > 1

    def part_wkt(pts: List) -> str:
        if has_z:
            return ", ".join(f"{float(p.x())} {float(p.y())} {float(p.z())}" for p in pts)
        return ", ".join(f"{float(p.x())} {float(p.y())}" for p in pts)

    if not is_multi:
        if has_z:
            wkt = f"LINESTRING Z ({part_wkt(ordered_parts[0])})"
        else:
            wkt = f"LINESTRING ({part_wkt(ordered_parts[0])})"
        return QgsGeometry.fromWkt(wkt)

    parts_txt = ", ".join(f"({part_wkt(pts)})" for pts in ordered_parts)
    if has_z:
        wkt = f"MULTILINESTRING Z ({parts_txt})"
    else:
        wkt = f"MULTILINESTRING ({parts_txt})"
    return QgsGeometry.fromWkt(wkt)


def _alongs_per_vertex_ordered_geom(work_geom: QgsGeometry) -> Tuple[List[float], float]:
    """
    Assumes geometry parts are already ordered/oriented as a chain.
    Returns (alongs_per_vertex, total_length) in work units (meters in GEODESIC).
    Does NOT add gaps between parts (jump keeps same along).
    """
    alongs: List[float] = []
    acc = 0.0

    parts = [work_geom.constGet()] if not work_geom.isMultipart() else list(work_geom.constParts())

    first_vertex = True
    prev_xy: Optional[QgsPointXY] = None

    for part in parts:
        # get vertices as sequence
        verts = [v for v in part.vertices()]
        if not verts:
            continue

        for k, v in enumerate(verts):
            pxy = QgsPointXY(float(v.x()), float(v.y()))
            if first_vertex:
                alongs.append(acc)
                prev_xy = pxy
                first_vertex = False
                continue

            # if we are at the first vertex of a NEW part, we "jump" with no added distance
            if k == 0:
                alongs.append(acc)
                prev_xy = pxy
                continue

            # normal within-part accumulation
            acc += _dist_xy(prev_xy, pxy) if prev_xy is not None else 0.0
            alongs.append(acc)
            prev_xy = pxy

    return alongs, float(acc)


# -------------------------
# Algorithm
# -------------------------

class CalibrateLinesFromDistance(QgsProcessingAlgorithm):
    INPUT_LINES = "INPUT_LINES"

    M_UNITS_OUT = "M_UNITS_OUT"      # output M units
    START_M = "START_M"              # in output units
    REVERSE = "REVERSE"
    OVERWRITE = "OVERWRITE"

    LENGTH_MODE = "LENGTH_MODE"      # advanced: AUTO/PLANAR/GEODESIC

    OUTPUT_LINES = "OUTPUT_LINES"

    # statuses / log types
    ST_OK = "OK"
    ST_SKIPPED_HAS_M = "SKIPPED_HAS_M"
    ST_BAD_GEOM = "BAD_GEOMETRY"
    ST_ZERO_LENGTH = "ZERO_LENGTH"
    ST_M_LOST = "M_LOST"

    def name(self):
        return "calibrate_lines_from_distance"

    def displayName(self):
        return "Calibrate lines from distance"

    def group(self):
        return "Calibrate M geometry"

    def groupId(self):
        return "calibrate_m_geometry"

    def createInstance(self):
        return CalibrateLinesFromDistance()

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LINES,
                self.tr("Input line layer"),
                [QgsProcessing.TypeVectorLine],
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.M_UNITS_OUT,
                self.tr("M units (output)"),
                options=[self.tr("Meters (m)"), self.tr("Kilometers (km)")],
                defaultValue=0,  # meters
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.START_M,
                self.tr("Start M value (in output units)"),
                QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.REVERSE,
                self.tr("Reverse direction (M values decrease along the geometry direction)"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.OVERWRITE,
                self.tr("Overwrite existing M values"),
                defaultValue=False,
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterEnum(
                    self.LENGTH_MODE,
                    self.tr("Length calculation mode"),
                    options=[
                        self.tr("Auto (recommended)"),
                        self.tr("Planar"),
                        self.tr("Geodesic (local projected CRS)"),
                    ],
                    defaultValue=0,
                )
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LINES,
                self.tr("Calibrated lines (LineStringM / MultiLineStringM)"),
            )
        )

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("calibrate_lines_from_distance")

    # --- internal conversions

    @staticmethod
    def _len_to_m_units(length_meters: float, m_units_out: int) -> float:
        return float(length_meters) if m_units_out == 0 else float(length_meters) / 1000.0

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT_LINES, context)
        if src is None:
            raise QgsProcessingException(self.tr("Invalid input line layer."))

        m_units_out = int(self.parameterAsEnum(parameters, self.M_UNITS_OUT, context))  # 0=m, 1=km
        start_m_out = float(self.parameterAsDouble(parameters, self.START_M, context))
        reverse = self.parameterAsBoolean(parameters, self.REVERSE, context)
        overwrite = self.parameterAsBoolean(parameters, self.OVERWRITE, context)
        length_mode = int(self.parameterAsEnum(parameters, self.LENGTH_MODE, context))

        layer_crs = src.sourceCrs()

        # Decide working CRS for distance computations
        use_geodesic = False
        if length_mode == MODE_GEODESIC:
            use_geodesic = True
        elif length_mode == MODE_AUTO:
            use_geodesic = _is_geographic(layer_crs)
        else:
            use_geodesic = False

        # Output fields
        out_fields = QgsFields()
        for f in src.fields():
            out_fields.append(f)
        out_fields.append(QgsField("N_SEGS", QVariant.Int))       # number of parts in input feature
        out_fields.append(QgsField("M_START", QVariant.Double))   # per-output-part
        out_fields.append(QgsField("M_END", QVariant.Double))     # per-output-part
        out_fields.append(QgsField("LEN_M", QVariant.Double))     # per-output-part length in output units
        out_fields.append(QgsField("STATUS", QVariant.String))

        # Output sink:
        # We output one feature per part, so force LineString(M/ZM) output.
        out_wkb = QgsWkbTypes.LineString
        if QgsWkbTypes.hasZ(src.wkbType()):
            out_wkb = QgsWkbTypes.addZ(out_wkb)
        out_wkb = QgsWkbTypes.addM(out_wkb)

        (sink, sink_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT_LINES,
            context,
            out_fields,
            out_wkb,
            layer_crs,
        )

        feats: List[QgsFeature] = [f for f in src.getFeatures()]
        total = len(feats) or 0

        # Counters
        n_ok = 0
        n_warn = 0
        n_crit = 0
        warn_counts: Dict[str, int] = {}
        crit_counts: Dict[str, int] = {}

        def _emit_feature(base_feat: QgsFeature, geom_out: QgsGeometry, n_segs_in: int,
                          m_start, m_end, len_m, status: str):
            out_attrs = list(base_feat.attributes()) + [int(n_segs_in), m_start, m_end, len_m, status]
            out_f = QgsFeature(out_fields)
            out_f.setGeometry(geom_out)
            out_f.setAttributes(out_attrs)
            sink.addFeature(out_f, QgsFeatureSink.FastInsert)

        for i, f in enumerate(feats):
            if feedback.isCanceled():
                break
            if total:
                feedback.setProgress(int(100 * i / total))

            geom_in = f.geometry()
            fid = str(f.id())

            # Extract parts once (used for traceability)
            parts_pts = _extract_parts_points(geom_in)
            n_segs_in = int(len(parts_pts)) if parts_pts else 0

            # Basic geometry validation
            if geom_in is None or geom_in.isEmpty() or not geom_is_line(geom_in) or not parts_pts:
                status = self.ST_BAD_GEOM
                n_crit += 1
                crit_counts[status] = crit_counts.get(status, 0) + 1
                feedback.reportError(
                    f"[Calibrate lines from distance] FID={fid} -> CRITICAL={status}",
                    fatalError=False,
                )
                _emit_feature(f, geom_in, 0, None, None, None, status)
                continue

            # Skip if has M and overwrite is False
            if (not overwrite) and has_any_m(geom_in):
                status = self.ST_SKIPPED_HAS_M
                n_warn += 1
                warn_counts[status] = warn_counts.get(status, 0) + 1
                feedback.pushWarning(
                    f"[Calibrate lines from distance] FID={fid} -> WARNING={status}"
                )
                _emit_feature(f, geom_in, n_segs_in, None, None, None, status)
                continue

            # Build per-feature working transform (geodesic mode -> local UTM)
            to_work: Optional[QgsCoordinateTransform] = None
            if use_geodesic:
                local_crs = _local_projected_crs_for_geom(geom_in, layer_crs, context)
                try:
                    to_work = QgsCoordinateTransform(layer_crs, local_crs, context.transformContext())
                except Exception:
                    to_work = None

            # Determine chain order for parts (to keep M continuous across parts)
            if n_segs_in == 1:
                order = [(0, False)]
            else:
                tol = 0.01 if use_geodesic else 0.0
                order = _order_parts_nearest(parts_pts, to_work, tol)

            # Build ordered/oriented parts
            ordered_parts: List[List] = []
            for idx, rev in order:
                pts = parts_pts[int(idx)]
                ordered_parts.append(list(reversed(pts)) if bool(rev) else pts)

            # Precompute per-part lengths in WORK CRS (meters in GEODESIC)
            tol_dist = 0.01 if use_geodesic else 0.0
            part_lengths: List[float] = []
            for pts in ordered_parts:
                part_geom_layer = _geom_from_ordered_parts(geom_in, [pts])
                part_work = QgsGeometry(part_geom_layer)
                if to_work is not None:
                    try:
                        part_work.transform(to_work)
                    except Exception:
                        pass
                alongs_p = continuous_along_distances(part_work, tol=tol_dist, add_gaps=False)
                part_len = float(alongs_p[-1]) if alongs_p else 0.0
                part_lengths.append(part_len)

            total_len = float(sum(part_lengths))

            cum = 0.0  # accumulated distance at part start (work units)
            for part_idx, pts in enumerate(ordered_parts):
                part_geom_layer = _geom_from_ordered_parts(geom_in, [pts])

                part_work = QgsGeometry(part_geom_layer)
                if to_work is not None:
                    try:
                        part_work.transform(to_work)
                    except Exception:
                        pass

                alongs = continuous_along_distances(part_work, tol=tol_dist, add_gaps=False)
                if not alongs:
                    status = self.ST_BAD_GEOM
                    n_crit += 1
                    crit_counts[status] = crit_counts.get(status, 0) + 1
                    feedback.reportError(
                        f"[Calibrate lines from distance] FID={fid} -> CRITICAL={status}",
                        fatalError=False,
                    )
                    _emit_feature(f, part_geom_layer, n_segs_in, None, None, None, status)
                    continue

                # Global along for continuity across parts
                m_values: List[float] = []
                for a in alongs:
                    if reverse:
                        global_along = max(0.0, total_len - (cum + float(a)))
                    else:
                        global_along = cum + float(a)

                    m_values.append(
                        float(start_m_out + self._len_to_m_units(global_along, m_units_out))
                    )

                out_geom = rebuild_geom_with_m(part_geom_layer, m_values)

                status = self.ST_OK

                # Check if M survived
                if (not QgsWkbTypes.hasM(out_geom.wkbType())) or (not has_any_m(out_geom)):
                    status = self.ST_M_LOST
                    n_warn += 1
                    warn_counts[status] = warn_counts.get(status, 0) + 1
                    QgsMessageLog.logMessage(
                        f"[Calibrate lines from distance] FID={fid} -> WARNING={status} "
                        + self.tr("(output has no real M values)"),
                        "CalibrateLinesFromDistance",
                        Qgis.Warning,
                    )

                part_len_work = float(alongs[-1]) if alongs else 0.0
                if part_len_work <= 0.0:
                    status = self.ST_ZERO_LENGTH if status == self.ST_OK else status
                    n_warn += 1
                    warn_counts[self.ST_ZERO_LENGTH] = warn_counts.get(self.ST_ZERO_LENGTH, 0) + 1
                    feedback.pushWarning(
                        f"[Calibrate lines from distance] FID={fid} -> WARNING={self.ST_ZERO_LENGTH}"
                    )

                len_m = self._len_to_m_units(part_len_work, m_units_out)
                m_start = float(m_values[0]) if m_values else None
                m_end = float(m_values[-1]) if m_values else None

                _emit_feature(f, out_geom, n_segs_in, m_start, m_end, float(len_m), status)

                if status == self.ST_OK:
                    n_ok += 1

                cum += part_len_work

        # Summary
        feedback.pushInfo(f"[Calibrate lines from distance] OK={n_ok}  WARNINGS={n_warn}  CRITICALS={n_crit}")

        if warn_counts:
            parts = [f"{k}:{v}" for k, v in sorted(warn_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning(self.tr("[Calibrate lines from distance] Warnings by type: ") + ", ".join(parts))

        if crit_counts:
            parts = [f"{k}:{v}" for k, v in sorted(crit_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning(self.tr("[Calibrate lines from distance] Criticals by type: ") + ", ".join(parts))

        return {self.OUTPUT_LINES: sink_id}