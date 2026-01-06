# -*- coding: utf-8 -*-
from __future__ import annotations

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterDefinition,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsFeatureSink,
)
from qgis.PyQt.QtCore import QVariant, QCoreApplication

from ..utils import (
    pk_to_km,
    format_pk,
    find_distance_for_m_km,
    global_m_range_km,
    is_pk_covered_by_any_geom,
    nearest_available_m_km,
    codes_to_str,
)


def _adv(p):
    p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return p


class LocatePoints(QgsProcessingAlgorithm):
    """
    Locate points (manual)

    Locate 1 or 2 points on a calibrated line layer (LineStringM/MultiLineStringM),
    with the same behavior as LocatePointsFromTable:
      - ADJUSTED / ADJUST_REASON unificado
      - Advanced option for gaps (snap to the nearest available PK)
      - Optional issues table (only if there are issues)
    """

    INPUT_LINES = "INPUT_LINES"
    ROUTE_ID_FIELD = "ROUTE_ID_FIELD"

    M_UNITS = "M_UNITS"
    TOLERANCE_KM = "TOLERANCE_KM"

    SNAP_TO_NEAREST_AVAILABLE = "SNAP_TO_NEAREST_AVAILABLE"
    GENERATE_ISSUES = "GENERATE_ISSUES"

    # Point 1 (required)
    ROUTE1 = "ROUTE1"
    PK1 = "PK1"
    PT1_ID = "PT1_ID"

    # Point 2 (optional, outside Advanced; controlled by a switch)
    DO_SECOND = "DO_SECOND"
    ROUTE2 = "ROUTE2"
    PK2 = "PK2"
    PT2_ID = "PT2_ID"

    OUTPUT_POINTS = "OUTPUT_POINTS"
    OUTPUT_ISSUES = "OUTPUT_ISSUES"

    # Unified adjustment
    ADJ_OUT_OF_RANGE = "OUT_OF_RANGE"
    ADJ_GAP_SNAP = "GAP_SNAP"

    # Criticals
    ERR_NO_ROUTE = "NO_ROUTE"
    ERR_PK_INVALID = "PK_INVALID"
    ERR_NO_M_RANGE = "NO_M_RANGE"
    ERR_NO_MATCH = "NO_MATCH"

    def name(self):
        return "locate_points"

    def displayName(self):
        return "Locate points"

    def group(self):
        return "Locate points (requires M geometry)"

    def groupId(self):
        return "locate_points"

    def createInstance(self):
        return LocatePoints()

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LINES,
                self.tr("Calibrated line layer (M)"),
                [QgsProcessing.TypeVectorLine],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.ROUTE_ID_FIELD,
                self.tr("Route identifier field (ROUTE_ID) in the line layer"),
                parentLayerParameterName=self.INPUT_LINES,
                type=QgsProcessingParameterField.String,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.M_UNITS,
                self.tr("M units"),
                options=[self.tr("Meters (m)"), self.tr("Kilometers (km)")],
                defaultValue=0,
            )
        )

        # Advanced (consistent with the “from table” version)
        self.addParameter(
            _adv(
                QgsProcessingParameterNumber(
                    self.TOLERANCE_KM,
                    self.tr("Tolerance (km) for M matching (snap/rounding)"),
                    QgsProcessingParameterNumber.Double,
                    defaultValue=0.00001,
                    minValue=0.0,
                )
            )
        )
        self.addParameter(
            _adv(
                QgsProcessingParameterBoolean(
                    self.SNAP_TO_NEAREST_AVAILABLE,
                    self.tr("Snap to the nearest available chainage point (PK) when the geometry is incomplete"),
                    defaultValue=True,
                )
            )
        )
        self.addParameter(
            _adv(
                QgsProcessingParameterBoolean(
                    self.GENERATE_ISSUES,
                    self.tr("Generate issues table (adjustments/criticals)"),
                    defaultValue=True,
                )
            )
        )

        # Point 1 (required)
        self.addParameter(QgsProcessingParameterString(self.ROUTE1, self.tr("Route identifier (point 1)")))
        self.addParameter(QgsProcessingParameterString(self.PK1, self.tr("Chainage (PK) (point 1) [km+mmm] or decimal number (km)")))
        self.addParameter(QgsProcessingParameterString(self.PT1_ID, self.tr("Additional point ID (EVENT_ID) (point 1)"), optional=True))

        # Point 2 (optional)
        self.addParameter(QgsProcessingParameterBoolean(self.DO_SECOND, self.tr("Define second point"), defaultValue=False))
        self.addParameter(QgsProcessingParameterString(self.ROUTE2, self.tr("Route identifier (point 2)"), optional=True))
        self.addParameter(QgsProcessingParameterString(self.PK2, self.tr("Chainage (PK) (point 2) [km+mmm] or decimal number (km)"), optional=True))
        self.addParameter(QgsProcessingParameterString(self.PT2_ID, self.tr("Additional point ID (EVENT_ID) (point 2)"), optional=True))

        # Salidas
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT_POINTS, self.tr("Located points")))
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ISSUES,
                self.tr("Issues (table)"),
                optional=True,
            )
        )

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("locate_points")

    def processAlgorithm(self, parameters, context, feedback):
        line_src = self.parameterAsSource(parameters, self.INPUT_LINES, context)
        if not line_src:
            raise QgsProcessingException(self.tr("Could not read the line layer."))
        if not QgsWkbTypes.hasM(line_src.wkbType()):
            raise QgsProcessingException(self.tr("The layer has no M values (it is not calibrated)."))

        route_id_field = self.parameterAsFields(parameters, self.ROUTE_ID_FIELD, context)[0]

        m_units_idx = self.parameterAsEnum(parameters, self.M_UNITS, context)
        m_unit = "m" if m_units_idx == 0 else "km"

        tolerance_km = float(self.parameterAsDouble(parameters, self.TOLERANCE_KM, context) or 0.0)
        snap_on_gaps = self.parameterAsBool(parameters, self.SNAP_TO_NEAREST_AVAILABLE, context)
        gen_issues = self.parameterAsBool(parameters, self.GENERATE_ISSUES, context)

        # -----------------------------
        # 1) Leer solicitudes (1..2)
        # -----------------------------
        requests = []

        def _read_point(route_param, pk_param, id_param, required: bool):
            rid = (self.parameterAsString(parameters, route_param, context) or "").strip()
            pk_txt = (self.parameterAsString(parameters, pk_param, context) or "").strip()
            pid = (self.parameterAsString(parameters, id_param, context) or "").strip()

            if required:
                if not rid:
                    raise QgsProcessingException(self.tr("Point 1: Route id cannot be empty."))
                if not pk_txt:
                    raise QgsProcessingException(self.tr("Point 1: PK cannot be empty."))
            else:
                if not rid or not pk_txt:
                    return

            try:
                pk_req_km = pk_to_km(pk_txt)
            except Exception:
                point_label = self.tr("Point 1") if required else self.tr("Point 2")
                raise QgsProcessingException(self.tr("{label}: invalid chainage (PK).").format(label=point_label))

            requests.append((rid, pk_req_km, pid))

        _read_point(self.ROUTE1, self.PK1, self.PT1_ID, required=True)

        if self.parameterAsBool(parameters, self.DO_SECOND, context):
            _read_point(self.ROUTE2, self.PK2, self.PT2_ID, required=False)

        include_pk_id = any(pid for (_rid, _pk, pid) in requests)

        # -----------------------------
        # 2) Index geometries by ROUTE_ID (single pass)
        # -----------------------------
        feedback.pushInfo(self.tr("Indexing line layer by ROUTE_ID…"))
        route_index: dict[str, list] = {}
        for f in line_src.getFeatures():
            if feedback.isCanceled():
                return {}
            g = f.geometry()
            if not g or g.isEmpty():
                continue
            rid = f[route_id_field]
            if rid is None:
                continue
            rid = str(rid).strip()
            if rid:
                route_index.setdefault(rid, []).append(g)

        if not route_index:
            raise QgsProcessingException(self.tr("Could not index routes (check ROUTE_ID)."))

        # -----------------------------
        # 3) Salida puntos
        # -----------------------------
        out_fields = QgsFields()
        out_fields.append(QgsField("ROUTE_ID", QVariant.String))
        if include_pk_id:
            out_fields.append(QgsField("EVENT_ID", QVariant.String))
        out_fields.append(QgsField("PK_REQ", QVariant.String))
        out_fields.append(QgsField("PK", QVariant.String))
        out_fields.append(QgsField("ADJUSTED", QVariant.Int))
        out_fields.append(QgsField("ADJUST_REASON", QVariant.String))
        out_fields.append(QgsField("STATUS", QVariant.String))

        sink_pts, out_pts_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_POINTS,
            context,
            out_fields,
            QgsWkbTypes.Point,
            line_src.sourceCrs(),
        )

        # -----------------------------
        # 4) Issues (only if needed)
        # -----------------------------
        issues = []

        def _add_issue(route_id: str, pk_id: str, pk_req: str,
                       adjusted: int, adjust_reason: str,
                       warnings: list[str], criticals: list[str]):
            issues.append({
                "ROUTE_ID": route_id,
                "EVENT_ID": pk_id,
                "PK_REQ": pk_req,
                "ADJUSTED": adjusted,
                "ADJUST_REASON": adjust_reason,
                "WARNINGS": warnings[:],
                "CRITICALS": criticals[:],
            })

        # -----------------------------
        # 5) Localizar puntos
        # -----------------------------
        def _locate_on_geoms(geoms: list, target_km: float):
            for g in geoms:
                d = find_distance_for_m_km(
                    g,
                    target_km,
                    m_unit=m_unit,
                    clamp=True,
                    tolerance_km=tolerance_km,
                )
                if d is None:
                    continue
                ig = g.interpolate(d)
                if ig and not ig.isEmpty():
                    return ig
            return None

        n_ok = 0
        n_adj = 0
        n_crit = 0
        added = 0

        for (rid, pk_req_km, pid) in requests:
            pk_req_str = format_pk(pk_req_km)

            geoms = route_index.get(rid)
            if not geoms:
                feedback.pushWarning(self.tr("Route '{route}': not found.").format(route=rid))
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pid, pk_req_str, 0, "", [], [self.ERR_NO_ROUTE])
                continue

            gmn, gmx = global_m_range_km(geoms, m_unit=m_unit)
            if gmn is None or gmx is None:
                feedback.pushWarning(self.tr("Route '{route}': no valid M range.").format(route=rid))
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pid, pk_req_str, 0, "", [], [self.ERR_NO_M_RANGE])
                continue

            adjust_reasons = set()
            pk_real_km = pk_req_km

            # OUT_OF_RANGE (global)
            if pk_real_km < gmn:
                pk_real_km = gmn
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)
            if pk_real_km > gmx:
                pk_real_km = gmx
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)

            # locate
            point_geom = None
            if is_pk_covered_by_any_geom(geoms, pk_real_km, m_unit=m_unit):
                point_geom = _locate_on_geoms(geoms, pk_real_km)
            else:
                # Hueco
                if snap_on_gaps:
                    near = nearest_available_m_km(geoms, pk_real_km, m_unit=m_unit)
                    if near is not None:
                        pk_real_km = near
                        adjust_reasons.add(self.ADJ_GAP_SNAP)
                        point_geom = _locate_on_geoms(geoms, pk_real_km)
                else:
                    feedback.pushWarning(self.tr("Route '{route}': chainage (PK) is in a gap and snapping is disabled.").format(route=rid))
                    n_crit += 1
                    if gen_issues:
                        _add_issue(rid, pid, pk_req_str, 0, "", [], [self.ERR_NO_MATCH])
                    continue

            if point_geom is None or point_geom.isEmpty():
                feedback.pushWarning(self.tr("Route '{route}': could not locate point.").format(route=rid))
                n_crit += 1
                if gen_issues:
                    adjusted = 1 if adjust_reasons else 0
                    _add_issue(rid, pid, pk_req_str, adjusted, ";".join(sorted(adjust_reasons)), [], [self.ERR_NO_MATCH])
                continue

            adjusted = 1 if adjust_reasons else 0
            adjust_reason = ";".join(sorted(adjust_reasons))
            if adjusted:
                n_adj += 1
                feedback.pushWarning(self.tr("Route '{route}': chainage (PK) adjusted ({reason}). {req} -> {real}.").format(route=rid, reason=adjust_reason, req=pk_req_str, real=format_pk(pk_real_km)))

            out_f = QgsFeature(out_fields)
            out_f.setGeometry(point_geom)
            out_f["ROUTE_ID"] = rid
            if include_pk_id:
                out_f["EVENT_ID"] = pid
            out_f["PK_REQ"] = pk_req_str
            out_f["PK"] = format_pk(pk_real_km)
            out_f["ADJUSTED"] = adjusted
            out_f["ADJUST_REASON"] = adjust_reason
            out_f["STATUS"] = "OK"
            sink_pts.addFeature(out_f, QgsFeatureSink.FastInsert)

            added += 1
            n_ok += 1

            if gen_issues and adjusted:
                _add_issue(rid, pid, pk_req_str, adjusted, adjust_reason, [], [])

        if added == 0:
            raise QgsProcessingException(self.tr("No points were generated (check routes, chainage (PK) values and M calibration)."))

        feedback.pushInfo(self.tr("=== Summary ==="))
        feedback.pushInfo(self.tr("OK points: {n}").format(n=n_ok))
        feedback.pushInfo(self.tr("Adjusted (ADJUSTED): {n}").format(n=n_adj))
        feedback.pushInfo(self.tr("Criticals: {n}").format(n=n_crit))

        result = {self.OUTPUT_POINTS: out_pts_id}

        # -----------------------------
        # 6) Tabla incidencias (solo si procede)
        # -----------------------------
        if gen_issues and issues:
            iss_fields = QgsFields()
            iss_fields.append(QgsField("ROUTE_ID", QVariant.String))
            if include_pk_id:
                iss_fields.append(QgsField("EVENT_ID", QVariant.String))
            iss_fields.append(QgsField("PK_REQ", QVariant.String))
            iss_fields.append(QgsField("ADJUSTED", QVariant.Int))
            iss_fields.append(QgsField("ADJUST_REASON", QVariant.String))
            iss_fields.append(QgsField("WARNINGS", QVariant.String))
            iss_fields.append(QgsField("CRITICALS", QVariant.String))

            sink_iss, out_iss_id = self.parameterAsSink(
                parameters,
                self.OUTPUT_ISSUES,
                context,
                iss_fields,
                QgsWkbTypes.NoGeometry,
                line_src.sourceCrs(),
            )

            for it in issues:
                f = QgsFeature(iss_fields)
                f["ROUTE_ID"] = it["ROUTE_ID"]
                if include_pk_id:
                    f["EVENT_ID"] = it["EVENT_ID"]
                f["PK_REQ"] = it["PK_REQ"]
                f["ADJUSTED"] = it["ADJUSTED"]
                f["ADJUST_REASON"] = it["ADJUST_REASON"]
                f["WARNINGS"] = codes_to_str(it["WARNINGS"])
                f["CRITICALS"] = codes_to_str(it["CRITICALS"])
                sink_iss.addFeature(f, QgsFeatureSink.FastInsert)

            result[self.OUTPUT_ISSUES] = out_iss_id
            feedback.pushInfo(self.tr("Issues table generated: {n} rows.").format(n=len(issues)))
        else:
            if gen_issues:
                feedback.pushInfo(self.tr("No issues: the issues table is not generated."))

        return result