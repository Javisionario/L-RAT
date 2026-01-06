# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from qgis.PyQt.QtCore import QVariant, QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterDistance,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsFeatureSink,
    QgsSpatialIndex,
)

from .calib_utils import (
    adv,
    safe_str,
    geom_is_point,
    geom_is_line,
    vertex_count,
    rebuild_geom_with_m,
)

# ---------------------------------------------------------
# Helpers (algorithm-specific)
# ---------------------------------------------------------

_PK_RE = re.compile(r"^\s*(\d+)\s*\+\s*(\d{1,3})\s*$")  # 12+345


def parse_pk_to_km_with_units(value, pk_units_in: int) -> Optional[float]:
    """
    pk_units_in:
      0 = Auto (heurística)
      1 = Meters (m)
      2 = Kilometers (km)
    Return: kilometers as float.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # km+mmm always wins
    m = _PK_RE.match(s)
    if m:
        km = float(m.group(1))
        mmm = float(m.group(2))
        if mmm >= 1000:
            return None
        return km + (mmm / 1000.0)

    s2 = s.replace(",", ".")
    try:
        v = float(s2)
    except Exception:
        return None

    if pk_units_in == 2:  # km
        return float(v)
    if pk_units_in == 1:  # m
        return float(v) / 1000.0

    # Auto heuristic
    if abs(v) >= 1000.0:
        return float(v) / 1000.0
    return float(v)


def _iter_vertices_as_qgspoint(geom: QgsGeometry) -> List[QgsPoint]:
    """
    ROBUSTO: no usa QgsPoint(v) (puede fallar según QGIS/tipo de vértice).
    Construye QgsPoint desde x/y y añade z/m si están disponibles.
    """
    pts: List[QgsPoint] = []
    if geom is None or geom.isEmpty():
        return pts

    parts = [geom.constGet()] if not geom.isMultipart() else list(geom.constParts())
    for part in parts:
        for v in part.vertices():
            x = float(v.x())
            y = float(v.y())
            p = QgsPoint(x, y)

            try:
                p.setZ(float(v.z()))
            except Exception:
                pass

            try:
                p.setM(float(v.m()))
            except Exception:
                pass

            pts.append(p)

    return pts


def _snap_endpoints_between_lines(geoms: List[QgsGeometry], tol: float) -> List[QgsGeometry]:
    if tol <= 0 or not geoms:
        return geoms

    endpoints: List[QgsPointXY] = []

    def _collect_endpoints(g: QgsGeometry):
        if g is None or g.isEmpty():
            return
        if not g.isMultipart():
            part = g.constGet()
            verts = list(part.vertices())
            if len(verts) >= 2:
                endpoints.append(QgsPointXY(verts[0]))
                endpoints.append(QgsPointXY(verts[-1]))
        else:
            for part in g.constParts():
                verts = list(part.vertices())
                if len(verts) >= 2:
                    endpoints.append(QgsPointXY(verts[0]))
                    endpoints.append(QgsPointXY(verts[-1]))

    for g in geoms:
        _collect_endpoints(g)

    if not endpoints:
        return geoms

    def _nearest_endpoint(p: QgsPointXY) -> QgsPointXY:
        best = None
        best_d2 = None
        for e in endpoints:
            dx = float(p.x() - e.x())
            dy = float(p.y() - e.y())
            d2 = dx * dx + dy * dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best = e
        if best is None:
            return p
        if best_d2 is not None and best_d2 <= tol * tol:
            return QgsPointXY(best)
        return p

    out: List[QgsGeometry] = []
    for g in geoms:
        if g is None or g.isEmpty():
            out.append(g)
            continue

        gg = QgsGeometry(g)
        if not gg.isMultipart():
            part = gg.constGet()
            verts = list(part.vertices())
            if len(verts) >= 2:
                p0 = QgsPointXY(verts[0])
                p1 = QgsPointXY(verts[-1])
                np0 = _nearest_endpoint(p0)
                np1 = _nearest_endpoint(p1)
                try:
                    gg.moveVertex(np0.x(), np0.y(), 0)
                    gg.moveVertex(np1.x(), np1.y(), len(verts) - 1)
                except Exception:
                    pass

        out.append(gg)

    return out


def _merge_route_geoms(geoms: List[QgsGeometry], snap_tol: float) -> Optional[QgsGeometry]:
    """
    Build per-route reference geometry:
    - endpoint snap
    - unaryUnion (fallback combine)
    - mergeLines if available
    """
    if not geoms:
        return None

    work = _snap_endpoints_between_lines([QgsGeometry(g) for g in geoms], tol=snap_tol)

    try:
        g = QgsGeometry.unaryUnion(work)
    except Exception:
        g = QgsGeometry(work[0])
        for gg in work[1:]:
            try:
                g = g.combine(gg)
            except Exception:
                pass

    try:
        if hasattr(g, "mergeLines"):
            ml = g.mergeLines()
            if ml and not ml.isEmpty():
                g = ml
    except Exception:
        pass

    return g


def _detect_forks_or_branches(route_geom: QgsGeometry, tol: float) -> Tuple[bool, str]:
    """
    Simple heuristic fork/branch detector.
    """
    if route_geom is None or route_geom.isEmpty():
        return False, ""
    if tol <= 0:
        tol = 1e-6

    nodes_deg: Dict[Tuple[int, int], int] = {}

    def _key(p: QgsPointXY) -> Tuple[int, int]:
        return (int(round(float(p.x()) / tol)), int(round(float(p.y()) / tol)))

    seg_count = 0
    parts = [route_geom.constGet()] if not route_geom.isMultipart() else list(route_geom.constParts())
    for part in parts:
        verts = list(part.vertices())
        if len(verts) < 2:
            continue
        for i in range(len(verts) - 1):
            a = QgsPointXY(verts[i])
            b = QgsPointXY(verts[i + 1])
            ka = _key(a)
            kb = _key(b)
            if ka == kb:
                continue
            nodes_deg[ka] = nodes_deg.get(ka, 0) + 1
            nodes_deg[kb] = nodes_deg.get(kb, 0) + 1
            seg_count += 1

    if seg_count == 0 or not nodes_deg:
        return False, ""

    max_deg = max(nodes_deg.values()) if nodes_deg else 0
    n_dangling = sum(1 for d in nodes_deg.values() if d == 1)

    if max_deg > 2:
        return True, f"FORK_DETECTED (max_degree={max_deg})"
    if n_dangling >= 4:
        return True, f"BRANCH_OR_MULTIPART (dangling_ends={n_dangling})"
    if route_geom.isMultipart():
        return True, "MULTIPART_REFERENCE (possible parallel axes or disjoint parts)"
    return False, ""


def _interp_piecewise(d: float, controls: List[Tuple[float, float]], mode: int) -> float:
    """
    mode:
      0 = extrapolate linear
      1 = clamp
      2 = null (NaN)
    """
    if not controls:
        return float("nan")
    controls = sorted(controls, key=lambda t: t[0])
    if len(controls) == 1:
        return float(controls[0][1])

    d_min, m_min = controls[0]
    d_max, m_max = controls[-1]

    if d <= d_min:
        if mode == 2:
            return float("nan")
        if mode == 1:
            return float(m_min)
        d0, m0 = controls[0]
        d1, m1 = controls[1]
        if d1 == d0:
            return float(m0)
        slope = (m1 - m0) / (d1 - d0)
        return float(m0 + slope * (d - d0))

    if d >= d_max:
        if mode == 2:
            return float("nan")
        if mode == 1:
            return float(m_max)
        d0, m0 = controls[-2]
        d1, m1 = controls[-1]
        if d1 == d0:
            return float(m1)
        slope = (m1 - m0) / (d1 - d0)
        return float(m1 + slope * (d - d1))

    for i in range(1, len(controls)):
        d1, m1 = controls[i]
        d0, m0 = controls[i - 1]
        if d <= d1:
            if d1 == d0:
                return float(m0)
            t = (d - d0) / (d1 - d0)
            return float(m0 + t * (m1 - m0))

    return float(m_max)


# ---------------------------------------------------------
# Data structures
# ---------------------------------------------------------

@dataclass
class _Ctrl:
    pt_id: str
    pk_raw: str
    m_value: float
    dist_axis: float
    snap_xy: QgsPointXY
    route_id_pts: str
    route_id_line: str
    line_fid: int
    unit_id: int
    part_idx: int


@dataclass
class _LineUnit:
    """
    A single-part line to be calibrated.
    For multipart inputs, one input feature becomes multiple _LineUnit entries.
    """
    unit_id: int
    src_fid: int
    part_idx: int
    N_SEGS: int
    feature: QgsFeature  # original feature (attributes come from here)
    geom: QgsGeometry    # single LineString geometry for this part
    route_id: str

# ---------------------------------------------------------
# Algorithm
# ---------------------------------------------------------

class CalibrateLineStringMFromPoints(QgsProcessingAlgorithm):
    INPUT_LINES = "INPUT_LINES"
    INPUT_POINTS = "INPUT_POINTS"
    PK_FIELD = "PK_FIELD"

    PK_UNITS_IN = "PK_UNITS_IN"
    GROUP_BY_ROUTE = "GROUP_BY_ROUTE"
    M_UNITS_OUT = "M_UNITS_OUT"
    MAX_DISTANCE = "MAX_DISTANCE"
    ENDPOINT_SNAP_TOL = "ENDPOINT_SNAP_TOL"
    EXTRAP_MODE = "EXTRAP_MODE"
    ROUTE_ID_POINTS = "ROUTE_ID_POINTS"
    ROUTE_ID_LINES = "ROUTE_ID_LINES"
    ADD_ROUTE_TO_OUTPUT = "ADD_ROUTE_TO_OUTPUT"

    FACTOR = "FACTOR"
    OFFSET = "OFFSET"
    GENERATE_ISSUES = "GENERATE_ISSUES"

    GENERATE_SNAPPED = "GENERATE_SNAPPED"

    OUTPUT_LINES = "OUTPUT_LINES"
    OUTPUT_SNAPPED = "OUTPUT_SNAPPED"
    OUTPUT_ISSUES = "OUTPUT_ISSUES"

    INC_BAD_GEOM = "BAD_GEOMETRY"
    INC_PK_INVALID = "PK_INVALID"
    INC_NO_ROUTE = "NO_ROUTE"
    INC_TOO_FAR = "TOO_FAR"
    INC_NO_MATCH = "NO_MATCH"
    INC_INSUFFICIENT = "INSUFFICIENT_POINTS"
    INC_DUPLICATE_POS = "DUPLICATE_POS_IGNORED"
    INC_NON_MONO = "NON_MONOTONIC_PK"
    INC_NO_ROUTE_REF = "NO_ROUTE_REFERENCE"
    INC_ROUTE_TOPO = "ROUTE_TOPOLOGY_WARNING"

    def name(self):
        return "calibrate_linestringm_from_points"

    def displayName(self):
        return "Calibrate lines from points/events"

    def group(self):
        return "Calibrate M geometry"

    def groupId(self):
        return "calibrate_m_geometry"

    def createInstance(self):
        return CalibrateLineStringMFromPoints()
    
    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LINES,
                self.tr("Input line layer to calibrate"),
                [QgsProcessing.TypeVectorLine],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_POINTS,
                self.tr("Reference point layer (events with chainage)"),
                [QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.PK_FIELD,
                self.tr("Chainage (PK) field in points layer"),
                parentLayerParameterName=self.INPUT_POINTS,
                type=QgsProcessingParameterField.Any,
                optional=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.PK_UNITS_IN,
                self.tr("Input chainage (PK) units (points layer)"),
                options=[
                    self.tr("Auto (detect 'km+mmm' or infer m/km)"),
                    self.tr("Meters (m)"),
                    self.tr("Kilometers (km)"),
                ],
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GROUP_BY_ROUTE,
                self.tr("Group by ROUTE_ID"),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.M_UNITS_OUT,
                self.tr("Output M units"),
                options=[self.tr("Meters (m)"), self.tr("Kilometers (km)")],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterDistance(
                self.MAX_DISTANCE,
                self.tr("Maximum search / projection distance"),
                parentParameterName=self.INPUT_POINTS,
                defaultValue=50.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.ENDPOINT_SNAP_TOL,
                self.tr("Endpoint snap tolerance (per-route referencing)"),
                QgsProcessingParameterNumber.Double,
                defaultValue=0.5,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.EXTRAP_MODE,
                self.tr("Behavior outside control range"),
                options=[
                    self.tr("Extrapolate linearly"),
                    self.tr("Clamp to range"),
                    self.tr("Set NULL (NaN)"),
                ],
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_ROUTE_TO_OUTPUT,
                self.tr("Add ROUTE_ID points field to output"),
                defaultValue=False,
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterField(
                    self.ROUTE_ID_POINTS,
                    self.tr("ROUTE_ID field in point layer"),
                    parentLayerParameterName=self.INPUT_POINTS,
                    type=QgsProcessingParameterField.Any,
                    optional=True,
                )
            )
        )
        self.addParameter(
            adv(
                QgsProcessingParameterField(
                    self.ROUTE_ID_LINES,
                    self.tr("ROUTE_ID field in line layer"),
                    parentLayerParameterName=self.INPUT_LINES,
                    type=QgsProcessingParameterField.Any,
                    optional=True,
                )
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterNumber(
                    self.FACTOR,
                    self.tr("Add factor: M' = M * factor"),
                    QgsProcessingParameterNumber.Double,
                    defaultValue=1.0,
                )
            )
        )
        self.addParameter(
            adv(
                QgsProcessingParameterNumber(
                    self.OFFSET,
                    self.tr("Add offset (in output M units): M' = M + offset"),
                    QgsProcessingParameterNumber.Double,
                    defaultValue=0.0,
                )
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterBoolean(
                    self.GENERATE_ISSUES,
                    self.tr("Generate issues table (only if there are issues)"),
                    defaultValue=False,
                )
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterBoolean(
                    self.GENERATE_SNAPPED,
                    self.tr("Generate projected point layer (control points)"),
                    defaultValue=False,
                )
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LINES,
                self.tr("Calibrated lines"),
            )
        )
        self.addParameter(
            adv(QgsProcessingParameterFeatureSink(self.OUTPUT_SNAPPED, self.tr("Projected points"), optional=True))
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ISSUES,
                self.tr("Issues (table)"),
                optional=True,
            )
        )

    def shortHelpString(self):

        from ..help.short_help import short_help
        return short_help("calibrate_lines_from_points")

    @staticmethod
    def _pk_km_to_m_units(pk_km: float, m_units_out: int) -> float:
        return float(pk_km) * 1000.0 if m_units_out == 0 else float(pk_km)

    @staticmethod
    def _m_to_meters(m_value: float, m_units_out: int) -> float:
        return float(m_value) if m_units_out == 0 else float(m_value) * 1000.0

    def processAlgorithm(self, parameters, context, feedback):
        lines_src = self.parameterAsSource(parameters, self.INPUT_LINES, context)
        pts_src = self.parameterAsSource(parameters, self.INPUT_POINTS, context)
        if lines_src is None:
            raise QgsProcessingException(self.tr("Invalid line layer."))
        if pts_src is None:
            raise QgsProcessingException(self.tr("Invalid point layer."))

        pk_field = self.parameterAsString(parameters, self.PK_FIELD, context)
        pk_units_in = int(self.parameterAsEnum(parameters, self.PK_UNITS_IN, context))

        group_by_route = self.parameterAsBoolean(parameters, self.GROUP_BY_ROUTE, context)
        m_units_out = int(self.parameterAsEnum(parameters, self.M_UNITS_OUT, context))
        max_dist = float(self.parameterAsDouble(parameters, self.MAX_DISTANCE, context))
        snap_tol = float(self.parameterAsDouble(parameters, self.ENDPOINT_SNAP_TOL, context))
        extrap_mode = int(self.parameterAsEnum(parameters, self.EXTRAP_MODE, context))
        restrict = bool(group_by_route)
        add_route = self.parameterAsBoolean(parameters, self.ADD_ROUTE_TO_OUTPUT, context)
        route_field_points = self.parameterAsString(parameters, self.ROUTE_ID_POINTS, context) or None
        route_field_lines = self.parameterAsString(parameters, self.ROUTE_ID_LINES, context) or None

        factor = float(self.parameterAsDouble(parameters, self.FACTOR, context))
        offset = float(self.parameterAsDouble(parameters, self.OFFSET, context))

        gen_issues = self.parameterAsBoolean(parameters, self.GENERATE_ISSUES, context)
        gen_snapped = self.parameterAsBoolean(parameters, self.GENERATE_SNAPPED, context)

        if group_by_route and (not route_field_points or not route_field_lines):
            raise QgsProcessingException(self.tr("'Group by ROUTE_ID' requieres ROUTE_ID fields in both points and lines."))
        if add_route and (not route_field_points):
            raise QgsProcessingException(self.tr("'Add ROUTE_ID to output' requieres ROUTE_ID field in points layer."))

        # Index lines (split multipart features into single-part units)
        idx = QgsSpatialIndex()
        unit_by_id: Dict[int, _LineUnit] = {}
        line_units: List[_LineUnit] = []
        route_to_line_geoms: Dict[str, List[QgsGeometry]] = {}

        next_unit_id = 0

        for lf in lines_src.getFeatures():
            g = lf.geometry()
            if g is None or g.isEmpty() or not geom_is_line(g):
                continue

            src_fid = int(lf.id())

            rid = ""
            if route_field_lines:
                try:
                    rid = safe_str(lf[route_field_lines])
                except Exception:
                    rid = ""

            parts = [g.constGet()] if not g.isMultipart() else list(g.constParts())
            N_SEGS = len(parts) if parts else 0
            if N_SEGS == 0:
                continue

            for part_idx, part in enumerate(parts):
                try:
                    part_geom = QgsGeometry(part.clone())
                except Exception:
                    continue

                if part_geom is None or part_geom.isEmpty():
                    continue

                next_unit_id += 1
                f_idx = QgsFeature()
                f_idx.setGeometry(part_geom)
                try:
                    f_idx.setId(next_unit_id)
                except Exception:
                    pass
                idx.addFeature(f_idx)

                u = _LineUnit(
                    unit_id=int(next_unit_id),
                    src_fid=int(src_fid),
                    part_idx=int(part_idx),
                    N_SEGS=int(N_SEGS),
                    feature=lf,
                    geom=part_geom,
                    route_id=rid,
                )
                unit_by_id[int(next_unit_id)] = u
                line_units.append(u)

                if group_by_route:
                    route_to_line_geoms.setdefault(rid, []).append(QgsGeometry(part_geom))

# Issues + counters
        issues_rows: List[Tuple[str, str, str, str, Optional[int], Optional[float], Optional[float], str]] = []
        warn_counts: Dict[str, int] = {}
        crit_counts: Dict[str, int] = {}
        line_warn_counts: Dict[str, int] = {}
        line_crit_counts: Dict[str, int] = {}

        def warn(tag: str, msg: str):
            warn_counts[tag] = warn_counts.get(tag, 0) + 1
            feedback.pushWarning(msg)

        def crit(tag: str, msg: str):
            crit_counts[tag] = crit_counts.get(tag, 0) + 1
            feedback.reportError(msg, fatalError=False)

        def lw(tag: str, msg: str):
            line_warn_counts[tag] = line_warn_counts.get(tag, 0) + 1
            feedback.pushWarning(msg)

        def lc(tag: str, msg: str):
            line_crit_counts[tag] = line_crit_counts.get(tag, 0) + 1
            feedback.reportError(msg, fatalError=False)

        # Build route refs (optional)
        route_ref: Dict[str, QgsGeometry] = {}
        if group_by_route:
            topo_tol = max(0.01 * max_dist, 1e-6)
            for rid, geoms in route_to_line_geoms.items():
                if not rid:
                    route_ref[rid] = QgsGeometry()
                    continue
                ref = _merge_route_geoms(geoms, snap_tol=snap_tol)
                route_ref[rid] = ref if ref is not None else QgsGeometry()

                if ref is None or ref.isEmpty():
                    warn(self.INC_NO_ROUTE_REF, f"[Calibrate lines from points] ROUTE_ID={rid} -> WARNING={self.INC_NO_ROUTE_REF}")
                    if gen_issues:
                        issues_rows.append((self.INC_NO_ROUTE_REF, "", rid, "", None, None, None, "Route reference could not be built"))
                    continue

                has_topo, note = _detect_forks_or_branches(ref, tol=topo_tol)
                if has_topo:
                    warn(self.INC_ROUTE_TOPO, f"[Calibrate lines from points] ROUTE_ID={rid} -> WARNING={self.INC_ROUTE_TOPO}: {note}")
                    if gen_issues:
                        issues_rows.append((self.INC_ROUTE_TOPO, "", rid, "", None, None, None, note))

        # Step 1: match points -> controls
        controls_by_target: Dict[str, List[_Ctrl]] = {}
        total_pts = pts_src.featureCount() or 0

        for i, pf in enumerate(pts_src.getFeatures()):
            if feedback.isCanceled():
                break
            if total_pts:
                feedback.setProgress(int(35 * i / total_pts))

            pt_id = str(pf.id())
            pg = pf.geometry()

            if pg is None or pg.isEmpty() or not geom_is_point(pg):
                crit(self.INC_BAD_GEOM, f"[Calibrate lines from points] PT_ID={pt_id} -> CRITICAL={self.INC_BAD_GEOM}")
                if gen_issues:
                    issues_rows.append((self.INC_BAD_GEOM, pt_id, "", "", None, None, None, "Invalid point geometry"))
                continue

            pk_raw = safe_str(pf[pk_field])
            pk_km = parse_pk_to_km_with_units(pk_raw, pk_units_in)
            if pk_km is None:
                crit(self.INC_PK_INVALID, f"[Calibrate lines from points] PT_ID={pt_id} PK={pk_raw} -> CRITICAL={self.INC_PK_INVALID}")
                if gen_issues:
                    ridp = safe_str(pf[route_field_points]) if route_field_points else ""
                    issues_rows.append((self.INC_PK_INVALID, pt_id, ridp, pk_raw, None, None, None, "Invalid PK"))
                continue

            # PK(km) -> M(units out), then factor/offset (in output units)
            m_val = self._pk_km_to_m_units(pk_km, m_units_out)
            m_val = (m_val * factor) + offset

            rid_point = safe_str(pf[route_field_points]) if route_field_points else ""
            if restrict and not rid_point:
                crit(self.INC_NO_ROUTE, f"[Calibrate lines from points] PT_ID={pt_id} -> CRITICAL={self.INC_NO_ROUTE}")
                if gen_issues:
                    issues_rows.append((self.INC_NO_ROUTE, pt_id, "", pk_raw, None, None, None, "Missing ROUTE_ID in point feature"))
                continue

            pxy = QgsPointXY(pg.asPoint())
            cand_ids = idx.nearestNeighbor(pxy, 12)

            best_unit: Optional[_LineUnit] = None
            best_unit_id: Optional[int] = None
            best_axis = None
            best_snap = None
            best_rid_line = ""

            had_any = False
            min_dist_any = None

            for unit_id in cand_ids:
                unit_id = int(unit_id)
                u = unit_by_id.get(unit_id)
                if u is None:
                    continue

                rid_line = u.route_id
                if restrict and rid_line != rid_point:
                    continue

                had_any = True
                lg = u.geom
                if lg is None or lg.isEmpty():
                    continue

                try:
                    dist2, closest_pt, _, _ = lg.closestSegmentWithContext(pxy)
                    axis = (float(dist2) ** 0.5) if dist2 is not None else float(lg.distance(pg))
                    snap = closest_pt if isinstance(closest_pt, QgsPointXY) else QgsPointXY(closest_pt)
                except Exception:
                    continue

                if min_dist_any is None or axis < min_dist_any:
                    min_dist_any = float(axis)

                if axis > max_dist:
                    continue

                if best_axis is None or axis < best_axis:
                    best_axis = float(axis)
                    best_snap = snap
                    best_unit_id = unit_id
                    best_unit = u
                    best_rid_line = rid_line

            if best_unit_id is None or best_axis is None or best_snap is None or best_unit is None:
                if had_any and min_dist_any is not None and min_dist_any > max_dist:
                    crit(self.INC_TOO_FAR, f"[Calibrate lines from points] PT_ID={pt_id} DIST={min_dist_any:.3f} -> CRITICAL={self.INC_TOO_FAR}")
                    if gen_issues:
                        issues_rows.append((self.INC_TOO_FAR, pt_id, rid_point, pk_raw, None, float(min_dist_any), None, "Closest line beyond MAX_DISTANCE"))
                else:
                    crit(self.INC_NO_MATCH, f"[Calibrate lines from points] PT_ID={pt_id} -> CRITICAL={self.INC_NO_MATCH}")
                    if gen_issues:
                        issues_rows.append((self.INC_NO_MATCH, pt_id, rid_point, pk_raw, None, None, None, "No valid match"))
                continue

            target_key = best_rid_line if group_by_route else str(best_unit_id)
            controls_by_target.setdefault(target_key, []).append(
                _Ctrl(
                    pt_id=pt_id,
                    pk_raw=pk_raw,
                    m_value=float(m_val),
                    dist_axis=float(best_axis),
                    snap_xy=best_snap,
                    route_id_pts=rid_point,
                    route_id_line=best_rid_line,
                    line_fid=int(best_unit.src_fid),
                    unit_id=int(best_unit_id),
                    part_idx=int(best_unit.part_idx),
                )
            )

# Output lines sink
        out_fields = QgsFields()
        for f in lines_src.fields():
            out_fields.append(f)

        out_route_field_name = None
        if add_route:
            out_route_field_name = "ROUTE_ID"
            if out_fields.indexFromName("ROUTE_ID") != -1:
                out_route_field_name = "ROUTE_ID_FROM_PTS"
            out_fields.append(QgsField(out_route_field_name, QVariant.String))

        out_fields.append(QgsField("N_SEGS", QVariant.Int))
        out_fields.append(QgsField("N_CTRL", QVariant.Int))
        out_fields.append(QgsField("M_START", QVariant.Double))
        out_fields.append(QgsField("M_END", QVariant.Double))
        out_fields.append(QgsField("M_LEN", QVariant.Double))
        out_fields.append(QgsField("LEN_GEOM", QVariant.Double))
        out_fields.append(QgsField("LEN_ERR_M", QVariant.Double))
        out_fields.append(QgsField("LEN_ERR_P", QVariant.Double))
        out_fields.append(QgsField("HAS_NULLM", QVariant.Int))
        out_fields.append(QgsField("STATUS", QVariant.String))

        (sink, sink_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT_LINES,
            context,
            out_fields,
            QgsWkbTypes.addM(QgsWkbTypes.singleType(lines_src.wkbType())),
            lines_src.sourceCrs(),
        )

        # ✅ Snapped sink ONLY if checkbox enabled
        snapped_sink = None
        snapped_sink_id = None
        snapped_fields_obj: Optional[QgsFields] = None

        if gen_snapped:
            snapped_fields_obj = QgsFields()
            snapped_fields_obj.append(QgsField("PT_ID", QVariant.String))
            snapped_fields_obj.append(QgsField("LINE_FID", QVariant.Int))
            snapped_fields_obj.append(QgsField("ROUTE_ID_LINE", QVariant.String))
            snapped_fields_obj.append(QgsField("ROUTE_ID_PTS", QVariant.String))
            snapped_fields_obj.append(QgsField("PK_RAW", QVariant.String))
            snapped_fields_obj.append(QgsField("M", QVariant.Double))
            snapped_fields_obj.append(QgsField("DIST_AXIS", QVariant.Double))
            snapped_fields_obj.append(QgsField("DIST_ALONG", QVariant.Double))

            (snapped_sink, snapped_sink_id) = self.parameterAsSink(
                parameters,
                self.OUTPUT_SNAPPED,
                context,
                snapped_fields_obj,
                QgsWkbTypes.Point,
                lines_src.sourceCrs(),
            )

        # Precompute route control pairs (route scope)
        route_ctrl_pairs: Dict[str, List[Tuple[float, float]]] = {}
        if group_by_route:
            for rid, ctrls in controls_by_target.items():
                ref = route_ref.get(rid)
                if ref is None or ref.isEmpty():
                    continue

                pairs: List[Tuple[float, float]] = []
                for c in ctrls:
                    try:
                        d = float(ref.lineLocatePoint(QgsGeometry.fromPointXY(c.snap_xy)))
                    except Exception:
                        continue
                    pairs.append((d, float(c.m_value)))

                    # ✅ Guarded snapped output
                    if snapped_sink is not None and snapped_fields_obj is not None:
                        fsp = QgsFeature(snapped_fields_obj)
                        fsp.setGeometry(QgsGeometry.fromPointXY(c.snap_xy))
                        fsp.setAttributes([c.pt_id, int(c.line_fid), c.route_id_line, c.route_id_pts, c.pk_raw, float(c.m_value), float(c.dist_axis), float(d)])
                        snapped_sink.addFeature(fsp, QgsFeatureSink.FastInsert)

                pairs.sort(key=lambda t: t[0])

                eps = 1e-6
                uniq: List[Tuple[float, float]] = []
                for (d, m) in pairs:
                    if not uniq or abs(d - uniq[-1][0]) > eps:
                        uniq.append((d, m))
                    else:
                        warn(self.INC_DUPLICATE_POS, f"[Calibrate lines from points] ROUTE_ID={rid} -> WARNING={self.INC_DUPLICATE_POS}")
                        if gen_issues:
                            issues_rows.append((self.INC_DUPLICATE_POS, "", rid, "", None, None, float(d), "Duplicate control position ignored"))
                pairs = uniq

                if len(pairs) >= 2 and any(pairs[i][1] < pairs[i - 1][1] for i in range(1, len(pairs))):
                    warn(self.INC_NON_MONO, f"[Calibrate lines from points] ROUTE_ID={rid} -> WARNING={self.INC_NON_MONO}")
                    if gen_issues:
                        issues_rows.append((self.INC_NON_MONO, "", rid, "", None, None, None, "Control PK values are not monotonic along route"))

                route_ctrl_pairs[rid] = pairs

        # Calibrate lines (per-part units)
        n_ok = 0
        total_units = len(line_units) or 0

        for j, u in enumerate(line_units):
            if feedback.isCanceled():
                break
            if total_units:
                feedback.setProgress(35 + int(65 * j / total_units))

            unit_id = int(u.unit_id)
            fid = int(u.src_fid)  # original feature id (for traceability/logging)
            geom = u.geom
            rid_line = u.route_id
            N_SEGS = int(u.N_SEGS)

            if geom is None or geom.isEmpty() or not geom_is_line(geom):
                status = self.INC_BAD_GEOM
                lc(status, f"[Calibrate lines from points] LINE_FID={fid} PART={u.part_idx} -> CRITICAL={status}")
                attrs = list(u.feature.attributes())
                if add_route:
                    attrs.append("")
                attrs.append(N_SEGS)
                attrs.extend([0, None, None, None, float(geom.length()) if geom else None, None, None, 0, status])
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(attrs)
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            # Select reference + control pairs
            if group_by_route:
                ref = route_ref.get(rid_line)
                ctrl_pairs = route_ctrl_pairs.get(rid_line, [])
                if ref is None or ref.isEmpty():
                    status = self.INC_NO_ROUTE_REF
                    lc(status, f"[Calibrate lines from points] LINE_FID={fid} PART={u.part_idx} ROUTE_ID={rid_line} -> CRITICAL={status}")
                    if gen_issues:
                        issues_rows.append((status, "", rid_line, "", fid, None, None, "Route reference not available"))
                    attrs = list(u.feature.attributes())
                    if add_route:
                        attrs.append("")
                    attrs.append(N_SEGS)
                    attrs.extend([0, None, None, None, float(geom.length()), None, None, 0, status])
                    out_f = QgsFeature(out_fields)
                    out_f.setGeometry(geom)
                    out_f.setAttributes(attrs)
                    sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                    continue
            else:
                ref = geom
                ctrls = controls_by_target.get(str(unit_id), [])
                ctrl_pairs = []
                for c in ctrls:
                    try:
                        d = float(ref.lineLocatePoint(QgsGeometry.fromPointXY(c.snap_xy)))
                    except Exception:
                        continue
                    ctrl_pairs.append((d, float(c.m_value)))

                    # Guarded snapped output
                    if snapped_sink is not None and snapped_fields_obj is not None:
                        fsp = QgsFeature(snapped_fields_obj)
                        fsp.setGeometry(QgsGeometry.fromPointXY(c.snap_xy))
                        fsp.setAttributes([c.pt_id, int(fid), rid_line, c.route_id_pts, c.pk_raw, float(c.m_value), float(c.dist_axis), float(d)])
                        snapped_sink.addFeature(fsp, QgsFeatureSink.FastInsert)

                ctrl_pairs.sort(key=lambda t: t[0])

                eps = 1e-6
                uniq: List[Tuple[float, float]] = []
                for (d, m) in ctrl_pairs:
                    if not uniq or abs(d - uniq[-1][0]) > eps:
                        uniq.append((d, m))
                    else:
                        lw(self.INC_DUPLICATE_POS, f"[Calibrate lines from points] LINE_FID={fid} PART={u.part_idx} -> WARNING={self.INC_DUPLICATE_POS}")
                        if gen_issues:
                            issues_rows.append((self.INC_DUPLICATE_POS, "", rid_line, "", fid, None, float(d), "Duplicate control position ignored"))
                ctrl_pairs = uniq

                if len(ctrl_pairs) >= 2 and any(ctrl_pairs[i][1] < ctrl_pairs[i - 1][1] for i in range(1, len(ctrl_pairs))):
                    lw(self.INC_NON_MONO, f"[Calibrate lines from points] LINE_FID={fid} PART={u.part_idx} -> WARNING={self.INC_NON_MONO}")
                    if gen_issues:
                        issues_rows.append((self.INC_NON_MONO, "", rid_line, "", fid, None, None, "Control PK values are not monotonic"))

            if len(ctrl_pairs) < 2:
                status = self.INC_INSUFFICIENT
                lc(status, f"[Calibrate lines from points] LINE_FID={fid} PART={u.part_idx} -> CRITICAL={status} (N_CTRL={len(ctrl_pairs)})")
                if gen_issues:
                    issues_rows.append((status, "", rid_line, "", fid, None, None, "Need at least 2 control points"))

                attrs = list(u.feature.attributes())
                route_out = ""
                if add_route:
                    for c in controls_by_target.get(str(unit_id), []):
                        if c.route_id_pts:
                            route_out = c.route_id_pts
                            break
                    attrs.append(route_out)

                attrs.append(N_SEGS)
                geom_len = float(geom.length())
                attrs.extend([len(ctrl_pairs), None, None, None, geom_len, None, None, 0, status])
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(attrs)
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            # Insert vertices at snapped control locations
            g_work = QgsGeometry(geom)

            if group_by_route:
                for c in controls_by_target.get(rid_line, []):
                    if c.unit_id != unit_id:
                        continue
                    try:
                        _, closest_pt, after_v, _ = g_work.closestSegmentWithContext(c.snap_xy)
                        cp = closest_pt if isinstance(closest_pt, QgsPointXY) else QgsPointXY(closest_pt)
                        g_work.insertVertex(cp.x(), cp.y(), after_v)
                    except Exception:
                        continue
            else:
                for c in controls_by_target.get(str(unit_id), []):
                    try:
                        _, closest_pt, after_v, _ = g_work.closestSegmentWithContext(c.snap_xy)
                        cp = closest_pt if isinstance(closest_pt, QgsPointXY) else QgsPointXY(closest_pt)
                        g_work.insertVertex(cp.x(), cp.y(), after_v)
                    except Exception:
                        continue

            verts = _iter_vertices_as_qgspoint(g_work)
            if not verts:
                status = self.INC_BAD_GEOM
                lc(status, f"[Calibrate lines from points] LINE_FID={fid} PART={u.part_idx} -> CRITICAL={status}")
                attrs = list(u.feature.attributes())
                if add_route:
                    attrs.append("")
                attrs.append(N_SEGS)
                attrs.extend([len(ctrl_pairs), None, None, None, float(geom.length()), None, None, 0, status])
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(attrs)
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            # Compute M values along reference
            m_values: List[float] = []
            has_null = 0
            for v in verts:
                pxy = QgsPointXY(float(v.x()), float(v.y()))
                try:
                    d = float(ref.lineLocatePoint(QgsGeometry.fromPointXY(pxy)))
                except Exception:
                    d = 0.0
                mv = _interp_piecewise(d, ctrl_pairs, mode=extrap_mode)
                if math.isnan(mv):
                    has_null = 1
                m_values.append(float(mv))

            if vertex_count(g_work) != len(m_values):
                status = self.INC_BAD_GEOM
                lc(status, f"[Calibrate lines from points] LINE_FID={fid} PART={u.part_idx} -> CRITICAL={status} (vertex mismatch)")
                attrs = list(u.feature.attributes())
                if add_route:
                    attrs.append("")
                attrs.append(N_SEGS)
                attrs.extend([len(ctrl_pairs), None, None, None, float(geom.length()), None, None, 0, status])
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(attrs)
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            # Rebuild with calib_utils
            out_geom = rebuild_geom_with_m(g_work, m_values)

            m_start = None if math.isnan(m_values[0]) else float(m_values[0])
            m_end = None if math.isnan(m_values[-1]) else float(m_values[-1])

            m_len = None
            if m_start is not None and m_end is not None:
                m_len = abs(m_end - m_start)

            geom_len = float(out_geom.length())
            len_err_m = None
            len_err_p = None
            if m_len is not None:
                m_len_meters = self._m_to_meters(m_len, m_units_out)
                len_err_m = abs(geom_len - float(m_len_meters))
                if geom_len > 0:
                    len_err_p = 100.0 * float(len_err_m) / float(geom_len)

            route_out = ""
            if add_route:
                if group_by_route:
                    counts: Dict[str, int] = {}
                    for c in controls_by_target.get(rid_line, []):
                        if c.route_id_pts:
                            counts[c.route_id_pts] = counts.get(c.route_id_pts, 0) + 1
                    route_out = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[0][0] if counts else ""
                else:
                    for c in controls_by_target.get(str(unit_id), []):
                        if c.route_id_pts:
                            route_out = c.route_id_pts
                            break

            attrs = list(u.feature.attributes())
            if add_route:
                attrs.append(route_out)
            attrs.append(N_SEGS)
            attrs.extend([len(ctrl_pairs), m_start, m_end, m_len, geom_len, len_err_m, len_err_p, has_null, "OK"])

            out_f = QgsFeature(out_fields)
            out_f.setGeometry(out_geom)
            out_f.setAttributes(attrs)
            sink.addFeature(out_f, QgsFeatureSink.FastInsert)
            n_ok += 1

        results = {self.OUTPUT_LINES: sink_id}
        if snapped_sink_id is not None:
            results[self.OUTPUT_SNAPPED] = snapped_sink_id

        # Issues table only if enabled and there are rows
        if gen_issues and issues_rows:
            issues_fields = QgsFields()
            issues_fields.append(QgsField("INC_TYPE", QVariant.String))
            issues_fields.append(QgsField("PT_ID", QVariant.String))
            issues_fields.append(QgsField("ROUTE_ID", QVariant.String))
            issues_fields.append(QgsField("PK_RAW", QVariant.String))
            issues_fields.append(QgsField("LINE_FID", QVariant.Int))
            issues_fields.append(QgsField("DIST_AXIS", QVariant.Double))
            issues_fields.append(QgsField("DIST_ALONG", QVariant.Double))
            issues_fields.append(QgsField("NOTE", QVariant.String))

            (issues_sink, issues_sink_id) = self.parameterAsSink(
                parameters,
                self.OUTPUT_ISSUES,
                context,
                issues_fields,
                QgsWkbTypes.NoGeometry,
                lines_src.sourceCrs(),
            )

            for (inc_type, pt_id, route_id, pk_raw, line_fid, dist_axis, dist_along, note) in issues_rows:
                feat = QgsFeature(issues_fields)
                feat.setAttributes([inc_type, pt_id, route_id, pk_raw, line_fid, dist_axis, dist_along, note])
                issues_sink.addFeature(feat, QgsFeatureSink.FastInsert)

            results[self.OUTPUT_ISSUES] = issues_sink_id

        # Summary
        feedback.pushInfo(f"[Calibrate lines from points] Lines OK={n_ok}")

        if warn_counts:
            parts = [f"{k}:{v}" for k, v in sorted(warn_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning("[Calibrate lines from points] Warnings (points/routes): " + ", ".join(parts))
        if crit_counts:
            parts = [f"{k}:{v}" for k, v in sorted(crit_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning("[Calibrate lines from points] Criticals (points/routes): " + ", ".join(parts))

        if line_warn_counts:
            parts = [f"{k}:{v}" for k, v in sorted(line_warn_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning("[Calibrate lines from points] Warnings (lines): " + ", ".join(parts))
        if line_crit_counts:
            parts = [f"{k}:{v}" for k, v in sorted(line_crit_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning("[Calibrate lines from points] Criticals (lines): " + ", ".join(parts))

        return results