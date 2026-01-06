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
    QgsGeometry,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsPointXY,
    QgsFeatureSink,
)
from qgis.PyQt.QtCore import QVariant, QCoreApplication

from ..utils import (
    pk_to_km,
    format_pk,
    normalize_pk_range,
    pk_distance_km,
    geom_length_km,
    round3,
    m_range_km,
    extract_segment_from_route_geoms,
    # Consistencia con table-based:
    global_m_range_km,
    is_pk_covered_by_any_geom,
    nearest_available_m_km,
    codes_to_str,
)


def _adv(p):
    """Marks a parameter as Advanced in Processing."""
    p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return p


class LocateManualSegments(QgsProcessingAlgorithm):
    """
    Extract segments (manual)

    Extrae 1 o 2 segmentos de una capa LineStringM/MultiLineStringM,
    aplicando el mismo comportamiento que los algoritmos basados en tabla:
      - Ajuste unificado ADJUSTED/ADJUST_REASON
      - Opción para huecos (snap a PK disponible más cercano)
      - Tabla de incidencias (opcional, solo si hay incidencias)
      - Puntos extremos con PK_REQ vs PK real y ADJUSTED por extremo
    """

    INPUT_LINES = "INPUT_LINES"
    ROUTE_ID_FIELD = "ROUTE_ID_FIELD"

    M_UNITS = "M_UNITS"
    TOLERANCE_KM = "TOLERANCE_KM"

    SNAP_TO_NEAREST_AVAILABLE = "SNAP_TO_NEAREST_AVAILABLE"
    GENERATE_ISSUES = "GENERATE_ISSUES"

    ROUTE1 = "ROUTE1"
    PK1_START = "PK1_START"
    PK1_END = "PK1_END"
    SEG1_ID = "SEG1_ID"

    DO_SECOND = "DO_SECOND"
    ROUTE2 = "ROUTE2"
    PK2_START = "PK2_START"
    PK2_END = "PK2_END"
    SEG2_ID = "SEG2_ID"

    GEN_POINTS = "GEN_POINTS"

    OUTPUT_LINES = "OUTPUT_LINES"
    OUTPUT_POINTS = "OUTPUT_POINTS"
    OUTPUT_ISSUES = "OUTPUT_ISSUES"

    # Ajuste unificado
    ADJ_OUT_OF_RANGE = "OUT_OF_RANGE"
    ADJ_GAP_SNAP = "GAP_SNAP"

    # Warnings reales (no “ajuste”)
    WARN_SEGMENT_SPLIT = "SEGMENT_SPLIT"

    # Críticos
    ERR_NO_ROUTE = "NO_ROUTE"
    ERR_PK_INVALID = "PK_INVALID"
    ERR_NO_M_RANGE = "NO_M_RANGE"
    ERR_NO_MATCH = "NO_MATCH"

    def name(self):
        return "locate_segments"

    def displayName(self):
        return "Locate segments"

    def group(self):
        return "Locate segments (requires M geometry)"

    def groupId(self):
        return "locate_segments"

    def createInstance(self):
        return LocateManualSegments()

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("locate-segments")

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
                self.tr("Route identifier field (ROUTE_ID)"),
                parentLayerParameterName=self.INPUT_LINES,
                type=QgsProcessingParameterField.String,
            )
        )

        # No avanzado: unidades
        self.addParameter(
            QgsProcessingParameterEnum(
                self.M_UNITS,
                self.tr("M units"),
                options=[self.tr("Meters (m)"), self.tr("Kilometers (km)")],
                defaultValue=0,
            )
        )

        # Avanzados comunes (consistentes con los otros)
        self.addParameter(
            _adv(
                QgsProcessingParameterNumber(
                    self.TOLERANCE_KM,
                    self.tr("Tolerance (km) for M snapping/rounding"),
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
                    self.tr("Snap to the nearest available chainage point (PK) when geometry is incomplete"),
                    defaultValue=True,
                )
            )
        )
        self.addParameter(
            _adv(
                QgsProcessingParameterBoolean(
                    self.GENERATE_ISSUES,
                    self.tr("Generate issues table if needed (adjustments/warnings/criticals)"),
                    defaultValue=True,
                )
            )
        )

        # Segmento 1 (obligatorio)
        self.addParameter(QgsProcessingParameterString(self.ROUTE1, self.tr("Route identifier (segment 1)")))
        self.addParameter(QgsProcessingParameterString(self.PK1_START, self.tr("Start chainage (PK) (segment 1)")))
        self.addParameter(QgsProcessingParameterString(self.PK1_END, self.tr("End chainage (PK) (segment 1)")))
        self.addParameter(QgsProcessingParameterString(self.SEG1_ID, self.tr("Additional segment ID (SEG_ID) (segment 1)"), optional=True))

        # Segmento 2 (opcional) -> NO avanzado, aparece justo después
        self.addParameter(QgsProcessingParameterBoolean(self.DO_SECOND, self.tr("Define second segment"), defaultValue=False))
        self.addParameter(QgsProcessingParameterString(self.ROUTE2, self.tr("Route identifier (segment 2)"), optional=True))
        self.addParameter(QgsProcessingParameterString(self.PK2_START, self.tr("Start chainage (PK) (segment 2)"), optional=True))
        self.addParameter(QgsProcessingParameterString(self.PK2_END, self.tr("End chainage (PK) (segment 2)"), optional=True))
        self.addParameter(QgsProcessingParameterString(self.SEG2_ID, self.tr("Additional segment ID (SEG_ID) (segment 2)"), optional=True))

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.GEN_POINTS,
                self.tr("Generate endpoint points layer (optional)"),
                defaultValue=False,
            )
        )

        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT_LINES, self.tr("Extracted segments (lines)")))
        self.addParameter(_adv(QgsProcessingParameterFeatureSink(self.OUTPUT_POINTS, self.tr("Endpoint points (optional)"))))
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ISSUES,
                self.tr("Issues (table)"),
                optional=True,
            )
        )

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
        make_points = self.parameterAsBool(parameters, self.GEN_POINTS, context)

        # -----------------------------
        # 1) Construir lista de segmentos solicitados
        # -----------------------------
        # Cada entrada: (route_id, pk_ini_req_n, pk_fin_req_n, seg_id, pk_ini_req_raw, pk_fin_req_raw)
        segments: list[tuple[str, float, float, str, float, float]] = []

        def _read_segment(route_param, pk_start_param, pk_end_param, seg_id_param, required: bool):
            route_id = (self.parameterAsString(parameters, route_param, context) or "").strip()
            pk_s_str = self.parameterAsString(parameters, pk_start_param, context) or ""
            pk_e_str = self.parameterAsString(parameters, pk_end_param, context) or ""

            if required and not route_id:
                raise QgsProcessingException(self.tr("Route identifier (segment 1) cannot be empty."))
            if not required and not route_id:
                return  # segmento 2 no definido

            try:
                pk_s = pk_to_km(pk_s_str)
                pk_e = pk_to_km(pk_e_str)
            except Exception:
                raise QgsProcessingException(self.tr("Invalid PK in segment {n}.").format(n=("1" if required else "2")))

            pk_ini_req_n, pk_fin_req_n = normalize_pk_range(pk_s, pk_e)
            seg_id = (self.parameterAsString(parameters, seg_id_param, context) or "").strip()
            segments.append((route_id, pk_ini_req_n, pk_fin_req_n, seg_id, pk_s, pk_e))

        _read_segment(self.ROUTE1, self.PK1_START, self.PK1_END, self.SEG1_ID, required=True)

        if self.parameterAsBool(parameters, self.DO_SECOND, context):
            # Si el usuario activa DO_SECOND, esperamos que haya datos; si no, se omite sin romper
            _read_segment(self.ROUTE2, self.PK2_START, self.PK2_END, self.SEG2_ID, required=False)

        if not segments:
            raise QgsProcessingException(self.tr("No valid segments were defined."))

        include_seg_id = any(seg_id for (_r, _a, _b, seg_id, _x, _y) in segments)

        # -----------------------------
        # 2) Indexar capa lineal por ROUTE_ID (una pasada)
        # -----------------------------
        feedback.pushInfo(self.tr("Indexing line layer by ROUTE_ID…"))
        route_index: dict[str, list[tuple[float, QgsGeometry]]] = {}

        for f in line_src.getFeatures():
            if feedback.isCanceled():
                return {}

            g = f.geometry()
            if not g or g.isEmpty():
                continue

            rid_val = f[route_id_field]
            if rid_val is None:
                continue

            rid = str(rid_val).strip()
            if not rid:
                continue

            mn, mx = m_range_km(g, m_unit=m_unit)
            if mn is None or mx is None:
                continue

            route_index.setdefault(rid, []).append((mn, g))

        for rid, lst in route_index.items():
            lst.sort(key=lambda t: t[0])

        # -----------------------------
        # 3) Salidas: líneas / puntos / incidencias
        # -----------------------------
        out_fields = QgsFields()
        out_fields.append(QgsField("ROUTE_ID", QVariant.String))
        if include_seg_id:
            out_fields.append(QgsField("SEG_ID", QVariant.String))
        out_fields.append(QgsField("PK_INI", QVariant.String))
        out_fields.append(QgsField("PK_FIN", QVariant.String))
        out_fields.append(QgsField("DIST_PK_KM", QVariant.Double))
        out_fields.append(QgsField("DIST_GEOM_KM", QVariant.Double))
        out_fields.append(QgsField("ADJUSTED", QVariant.Int))
        out_fields.append(QgsField("ADJUST_REASON", QVariant.String))
        out_fields.append(QgsField("N_PIECES", QVariant.Int))
        out_fields.append(QgsField("STATUS", QVariant.String))

        line_sink, line_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_LINES,
            context,
            out_fields,
            line_src.wkbType(),
            line_src.sourceCrs(),
        )

        pts_sink, pts_id, pts_fields = None, None, None
        if make_points:
            pts_fields = QgsFields()
            pts_fields.append(QgsField("ROUTE_ID", QVariant.String))
            if include_seg_id:
                pts_fields.append(QgsField("SEG_ID", QVariant.String))
            pts_fields.append(QgsField("PK_REQ", QVariant.String))
            pts_fields.append(QgsField("PK", QVariant.String))
            pts_fields.append(QgsField("ADJUSTED", QVariant.Int))
            pts_fields.append(QgsField("ADJUST_REASON", QVariant.String))

            pts_sink, pts_id = self.parameterAsSink(
                parameters,
                self.OUTPUT_POINTS,
                context,
                pts_fields,
                QgsWkbTypes.Point,
                line_src.sourceCrs(),
            )

        issues = []

        def _add_issue(route_id: str, seg_id: str, pk_ini_req: str, pk_fin_req: str,
                       adjusted: int, adjust_reason: str,
                       warnings: list[str], criticals: list[str]):
            issues.append({
                "ROUTE_ID": route_id,
                "SEG_ID": seg_id,
                "PK_INI_REQ": pk_ini_req,
                "PK_FIN_REQ": pk_fin_req,
                "ADJUSTED": adjusted,
                "ADJUST_REASON": adjust_reason,
                "WARNINGS": warnings[:],
                "CRITICALS": criticals[:],
            })

        def _reasons_for_end(pk_req_km: float, pk_used_km: float, gmn: float, gmx: float, geoms: list) -> tuple[int, str]:
            reasons = set()

            if pk_req_km < gmn or pk_req_km > gmx:
                reasons.add(self.ADJ_OUT_OF_RANGE)

            if snap_on_gaps and (not is_pk_covered_by_any_geom(geoms, pk_req_km, m_unit=m_unit)):
                if abs(pk_used_km - pk_req_km) > 1e-12:
                    reasons.add(self.ADJ_GAP_SNAP)

            adjusted = 1 if reasons or (abs(pk_used_km - pk_req_km) > 1e-12) else 0
            return adjusted, ";".join(sorted(reasons))

        # -----------------------------
        # 4) Ejecutar extracción
        # -----------------------------
        added = 0
        n_ok = n_adj = n_warn = n_crit = 0

        for (route_id, pk_ini_req_n, pk_fin_req_n, seg_id, _pk_s_raw, _pk_e_raw) in segments:
            geoms_with_mn = route_index.get(route_id)
            pk_ini_req_str = format_pk(pk_ini_req_n)
            pk_fin_req_str = format_pk(pk_fin_req_n)

            if not geoms_with_mn:
                feedback.pushWarning(self.tr("Route '{route}': ROUTE_ID not found in the line layer.").format(route=route_id))
                n_crit += 1
                if gen_issues:
                    _add_issue(route_id, seg_id, pk_ini_req_str, pk_fin_req_str, 0, "", [], [self.ERR_NO_ROUTE])
                continue

            route_geoms = [g for (_mn, g) in geoms_with_mn]

            gmn, gmx = global_m_range_km(route_geoms, m_unit=m_unit)
            if gmn is None or gmx is None:
                feedback.pushWarning(self.tr("Route '{route}': no valid M range.").format(route=route_id))
                n_crit += 1
                if gen_issues:
                    _add_issue(route_id, seg_id, pk_ini_req_str, pk_fin_req_str, 0, "", [], [self.ERR_NO_M_RANGE])
                continue

            adjust_reasons = set()

            pk_ini = pk_ini_req_n
            pk_fin = pk_fin_req_n

            # OUT_OF_RANGE (global)
            if pk_ini < gmn:
                pk_ini = gmn
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)
            if pk_fin > gmx:
                pk_fin = gmx
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)

            # GAP_SNAP (por extremo)
            snapped_any = False

            if not is_pk_covered_by_any_geom(route_geoms, pk_ini, m_unit=m_unit):
                if snap_on_gaps:
                    near = nearest_available_m_km(route_geoms, pk_ini, m_unit=m_unit)
                    if near is not None:
                        pk_ini = near
                        snapped_any = True
                else:
                    feedback.pushWarning(self.tr("Route '{route}': PK_INI falls in a gap and snapping is disabled -> skipped.").format(route=route_id))
                    n_crit += 1
                    if gen_issues:
                        _add_issue(route_id, seg_id, pk_ini_req_str, pk_fin_req_str, 0, "", [], [self.ERR_NO_MATCH])
                    continue

            if not is_pk_covered_by_any_geom(route_geoms, pk_fin, m_unit=m_unit):
                if snap_on_gaps:
                    near = nearest_available_m_km(route_geoms, pk_fin, m_unit=m_unit)
                    if near is not None:
                        pk_fin = near
                        snapped_any = True
                else:
                    feedback.pushWarning(self.tr("Route '{route}': PK_FIN falls in a gap and snapping is disabled -> skipped.").format(route=route_id))
                    n_crit += 1
                    if gen_issues:
                        _add_issue(route_id, seg_id, pk_ini_req_str, pk_fin_req_str, 0, "", [], [self.ERR_NO_MATCH])
                    continue

            if snapped_any:
                adjust_reasons.add(self.ADJ_GAP_SNAP)

            pk_ini, pk_fin = normalize_pk_range(pk_ini, pk_fin)

            seg_geom, adj_ini, adj_fin, n_pieces, _unused = extract_segment_from_route_geoms(
                route_geoms,
                pk_ini,
                pk_fin,
                line_src.wkbType(),
                m_unit=m_unit,
                tolerance_km=tolerance_km,
            )

            if not seg_geom or seg_geom.isEmpty():
                feedback.pushWarning(
                    self.tr("Route '{route}': could not extract segment for [{pk_ini} - {pk_fin}].").format(route=route_id, pk_ini=format_pk(pk_ini), pk_fin=format_pk(pk_fin))
                )
                n_crit += 1
                if gen_issues:
                    adjusted = 1 if adjust_reasons else 0
                    _add_issue(route_id, seg_id, pk_ini_req_str, pk_fin_req_str,
                               adjusted, ";".join(sorted(adjust_reasons)),
                               [], [self.ERR_NO_MATCH])
                continue

            warnings = []
            if n_pieces > 1:
                warnings.append(self.WARN_SEGMENT_SPLIT)
                n_warn += 1
                feedback.pushWarning(self.tr("Route '{route}': segment split into {n} pieces (discontinuities).").format(route=route_id, n=n_pieces))

            adjusted = 1 if adjust_reasons else 0
            adjust_reason = ";".join(sorted(adjust_reasons))
            if adjusted:
                n_adj += 1
                feedback.pushWarning(
                    self.tr(
                        "Route '{route}': PK adjusted ({reason}). "
                        "[{pk_ini_req}-{pk_fin_req}] -> [{pk_ini}-{pk_fin}]."
                    ).format(
                        route=route_id,
                        reason=adjust_reason,
                        pk_ini_req=pk_ini_req_str,
                        pk_fin_req=pk_fin_req_str,
                        pk_ini=format_pk(adj_ini),
                        pk_fin=format_pk(adj_fin),
                    )
                )

            dist_pk = round3(pk_distance_km(adj_ini, adj_fin))
            dist_geom = round3(geom_length_km(seg_geom, line_src.sourceCrs(), context.project()))

            out_f = QgsFeature(out_fields)
            out_f.setGeometry(seg_geom)
            out_f["ROUTE_ID"] = route_id
            if include_seg_id:
                out_f["SEG_ID"] = seg_id
            out_f["PK_INI"] = format_pk(adj_ini)
            out_f["PK_FIN"] = format_pk(adj_fin)
            out_f["DIST_PK_KM"] = dist_pk
            out_f["DIST_GEOM_KM"] = dist_geom
            out_f["ADJUSTED"] = adjusted
            out_f["ADJUST_REASON"] = adjust_reason
            out_f["N_PIECES"] = int(n_pieces)
            out_f["STATUS"] = "OK"
            line_sink.addFeature(out_f, QgsFeatureSink.FastInsert)
            added += 1
            n_ok += 1

            # Puntos extremos (2 puntos) con PK_REQ normalizado (evita falsos ajustados por orden de entrada)
            if pts_sink and pts_fields:
                ge = seg_geom.constGet()
                parts = [ge] if not seg_geom.isMultipart() else list(seg_geom.constParts())
                if parts:
                    v0 = list(parts[0].vertices())
                    vN = list(parts[-1].vertices())
                    if v0 and vN:
                        # INI
                        ini_adj, ini_reason = _reasons_for_end(pk_ini_req_n, adj_ini, gmn, gmx, route_geoms)
                        pf = QgsFeature(pts_fields)
                        pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(v0[0].x(), v0[0].y())))
                        pf["ROUTE_ID"] = route_id
                        if include_seg_id:
                            pf["SEG_ID"] = seg_id
                        pf["PK_REQ"] = format_pk(pk_ini_req_n)
                        pf["PK"] = format_pk(adj_ini)
                        pf["ADJUSTED"] = int(ini_adj)
                        pf["ADJUST_REASON"] = ini_reason
                        pts_sink.addFeature(pf, QgsFeatureSink.FastInsert)

                        # FIN
                        fin_adj, fin_reason = _reasons_for_end(pk_fin_req_n, adj_fin, gmn, gmx, route_geoms)
                        pf = QgsFeature(pts_fields)
                        pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(vN[-1].x(), vN[-1].y())))
                        pf["ROUTE_ID"] = route_id
                        if include_seg_id:
                            pf["SEG_ID"] = seg_id
                        pf["PK_REQ"] = format_pk(pk_fin_req_n)
                        pf["PK"] = format_pk(adj_fin)
                        pf["ADJUSTED"] = int(fin_adj)
                        pf["ADJUST_REASON"] = fin_reason
                        pts_sink.addFeature(pf, QgsFeatureSink.FastInsert)

            # Incidencias: registrar si hubo ajuste o warnings
            if gen_issues and (adjusted or warnings):
                _add_issue(route_id, seg_id, pk_ini_req_str, pk_fin_req_str, adjusted, adjust_reason, warnings, [])

        if added == 0:
            raise QgsProcessingException(self.tr("No segments were generated."))

        feedback.pushInfo(self.tr("=== Summary ==="))
        feedback.pushInfo(self.tr("Segments OK: {n}").format(n=n_ok))
        feedback.pushInfo(self.tr("Adjusted (ADJUSTED): {n}").format(n=n_adj))
        feedback.pushInfo(f"Warnings: {n_warn}")
        feedback.pushInfo(self.tr("Criticals: {n}").format(n=n_crit))

        result = {self.OUTPUT_LINES: line_id}
        if pts_id is not None:
            result[self.OUTPUT_POINTS] = pts_id

        # Tabla incidencias (solo si procede)
        if gen_issues and issues:
            iss_fields = QgsFields()
            iss_fields.append(QgsField("ROUTE_ID", QVariant.String))
            if include_seg_id:
                iss_fields.append(QgsField("SEG_ID", QVariant.String))
            iss_fields.append(QgsField("PK_INI_REQ", QVariant.String))
            iss_fields.append(QgsField("PK_FIN_REQ", QVariant.String))
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
                if include_seg_id:
                    f["SEG_ID"] = it["SEG_ID"]
                f["PK_INI_REQ"] = it["PK_INI_REQ"]
                f["PK_FIN_REQ"] = it["PK_FIN_REQ"]
                f["ADJUSTED"] = it["ADJUSTED"]
                f["ADJUST_REASON"] = it["ADJUST_REASON"]
                f["WARNINGS"] = codes_to_str(it["WARNINGS"])
                f["CRITICALS"] = codes_to_str(it["CRITICALS"])
                sink_iss.addFeature(f, QgsFeatureSink.FastInsert)

            result[self.OUTPUT_ISSUES] = out_iss_id
            feedback.pushInfo(self.tr("Issues table generated: {n} rows.").format(n=len(issues)))
        else:
            if gen_issues:
                feedback.pushInfo(self.tr("No issues: the issues table was not generated."))

        return result