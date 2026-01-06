# -*- coding: utf-8 -*-
from __future__ import annotations

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterDefinition,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsGeometry,
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
    global_m_range_km,
    is_pk_covered_by_any_geom,
    nearest_available_m_km,
    codes_to_str,
)


def _adv(p):
    """Marks a parameter as Advanced in Processing."""
    p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return p


class LocateSegmentsFromSegmentTable(QgsProcessingAlgorithm):
    INPUT_LINES = "INPUT_LINES"
    ROUTE_ID_FIELD = "ROUTE_ID_FIELD"

    SEG_TABLE = "SEG_TABLE"
    SEG_ROUTE_FIELD = "SEG_ROUTE_FIELD"
    SEG_PK1_FIELD = "SEG_PK1_FIELD"
    SEG_PK2_FIELD = "SEG_PK2_FIELD"
    SEG_EVENT_ID_FIELD = "SEG_EVENT_ID_FIELD"

    ADD_TABLE_FIELDS = "ADD_TABLE_FIELDS"

    M_UNITS = "M_UNITS"
    TOLERANCE_KM = "TOLERANCE_KM"

    SNAP_TO_NEAREST_AVAILABLE = "SNAP_TO_NEAREST_AVAILABLE"
    GENERATE_ISSUES = "GENERATE_ISSUES"
    OUTPUT_ISSUES = "OUTPUT_ISSUES"

    GEN_POINTS = "GEN_POINTS"

    OUTPUT_LINES = "OUTPUT_LINES"
    OUTPUT_POINTS = "OUTPUT_POINTS"

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

    # -------------------------
    # Identidad / UI
    # -------------------------
    def name(self):
        return "locate_segments_from_segment_table"

    def displayName(self):
        return "Locate segments from segment table"

    def group(self):
        return "Locate segments (requires M geometry)"

    def groupId(self):
        return "locate_segments"

    def createInstance(self):
        return LocateSegmentsFromSegmentTable()

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("locate-segments-from-segment-table")

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)

    # -------------------------
    # Parámetros
    # -------------------------
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

        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.SEG_TABLE,
                self.tr("Segments table (each row = 1 segment)"),
                [QgsProcessing.TypeVector],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.SEG_ROUTE_FIELD,
                self.tr("ROUTE_ID field in the table"),
                parentLayerParameterName=self.SEG_TABLE,
                type=QgsProcessingParameterField.Any,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.SEG_PK1_FIELD,
                self.tr("Start chainage (PK) field in the table [km+mmm] or decimal number (km)"),
                parentLayerParameterName=self.SEG_TABLE,
                type=QgsProcessingParameterField.Any,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.SEG_PK2_FIELD,
                self.tr("End chainage (PK) field in the table [km+mmm] or decimal number (km)"),
                parentLayerParameterName=self.SEG_TABLE,
                type=QgsProcessingParameterField.Any,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.SEG_EVENT_ID_FIELD,
                self.tr("Add segment ID field (EVENT_ID)"),
                parentLayerParameterName=self.SEG_TABLE,
                type=QgsProcessingParameterField.Any,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_TABLE_FIELDS,
                self.tr("Add table fields to the output"),
                defaultValue=False,
            )
        )

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
                    self.tr("Snap to the nearest available chainage (PK) when geometry is incomplete"),
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

    # -------------------------
    # Ejecución
    # -------------------------
    def processAlgorithm(self, parameters, context, feedback):
        line_src = self.parameterAsSource(parameters, self.INPUT_LINES, context)
        if not line_src:
            raise QgsProcessingException(self.tr("Could not read the line layer."))
        if not QgsWkbTypes.hasM(line_src.wkbType()):
            raise QgsProcessingException(self.tr("The layer has no M values (it is not calibrated)."))

        seg_src = self.parameterAsSource(parameters, self.SEG_TABLE, context)
        if not seg_src:
            raise QgsProcessingException(self.tr("Could not read the segments table."))

        route_id_field = self.parameterAsFields(parameters, self.ROUTE_ID_FIELD, context)[0]
        seg_route_field = self.parameterAsFields(parameters, self.SEG_ROUTE_FIELD, context)[0]
        seg_pk1_field = self.parameterAsFields(parameters, self.SEG_PK1_FIELD, context)[0]
        seg_pk2_field = self.parameterAsFields(parameters, self.SEG_PK2_FIELD, context)[0]

        ev_fields = self.parameterAsFields(parameters, self.SEG_EVENT_ID_FIELD, context)
        event_id_field = ev_fields[0] if ev_fields else None

        add_table_fields = self.parameterAsBool(parameters, self.ADD_TABLE_FIELDS, context)

        m_units_idx = self.parameterAsEnum(parameters, self.M_UNITS, context)
        m_unit = "m" if m_units_idx == 0 else "km"

        tolerance_km = float(self.parameterAsDouble(parameters, self.TOLERANCE_KM, context) or 0.0)
        snap_on_gaps = self.parameterAsBool(parameters, self.SNAP_TO_NEAREST_AVAILABLE, context)
        gen_issues = self.parameterAsBool(parameters, self.GENERATE_ISSUES, context)
        make_points = self.parameterAsBool(parameters, self.GEN_POINTS, context)

        # 1) Indexar capa lineal por ROUTE_ID
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

        if not route_index:
            raise QgsProcessingException(self.tr("Could not index routes (check ROUTE_ID and M calibration)."))

        # 2) Salida líneas
        out_fields = QgsFields()
        out_fields.append(QgsField("ROUTE_ID", QVariant.String))
        if event_id_field:
            out_fields.append(QgsField("EVENT_ID", QVariant.String))
        out_fields.append(QgsField("PK_INI", QVariant.String))
        out_fields.append(QgsField("PK_FIN", QVariant.String))
        out_fields.append(QgsField("DIST_PK_KM", QVariant.Double))
        out_fields.append(QgsField("DIST_GEOM_KM", QVariant.Double))
        out_fields.append(QgsField("ADJUSTED", QVariant.Int))
        out_fields.append(QgsField("ADJUST_REASON", QVariant.String))
        out_fields.append(QgsField("N_PIECES", QVariant.Int))
        out_fields.append(QgsField("STATUS", QVariant.String))

        table_fields_map: list[tuple[str, str]] = []
        if add_table_fields:
            reserved = {f.name() for f in out_fields}
            for fld in seg_src.fields():
                src = fld.name()
                out = f"T_{src}" if (src in reserved or out_fields.indexFromName(src) != -1) else src
                out_fields.append(QgsField(out, fld.type(), fld.typeName(), fld.length(), fld.precision()))
                table_fields_map.append((out, src))

        line_sink, line_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_LINES,
            context,
            out_fields,
            line_src.wkbType(),
            line_src.sourceCrs(),
        )

        # 3) Puntos extremos (opcional) -> ahora con PK_REQ y ADJUSTED por extremo
        pts_sink, pts_id, pts_fields = None, None, None
        if make_points:
            pts_fields = QgsFields()
            pts_fields.append(QgsField("ROUTE_ID", QVariant.String))
            if event_id_field:
                pts_fields.append(QgsField("EVENT_ID", QVariant.String))
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

        # 4) Incidencias
        issues = []

        def _add_issue(route_id: str, pk_ini_req: str, pk_fin_req: str, ev: str,
                       adjusted: int, adjust_reason: str,
                       warnings: list[str], criticals: list[str]):
            issues.append({
                "ROUTE_ID": route_id,
                "PK_INI_REQ": pk_ini_req,
                "PK_FIN_REQ": pk_fin_req,
                "EVENT_ID": ev,
                "ADJUSTED": adjusted,
                "ADJUST_REASON": adjust_reason,
                "WARNINGS": warnings[:],
                "CRITICALS": criticals[:],
            })

        n_ok = 0
        n_adj = 0
        n_warn = 0
        n_crit = 0
        added = 0

        total = seg_src.featureCount() if seg_src.featureCount() >= 0 else 0
        done = 0

        def _reasons_for_end(pk_req_km: float, pk_used_km: float, gmn: float, gmx: float, geoms: list) -> tuple[int, str]:
            """
            Devuelve (adjusted_int, adjust_reason_str) para un extremo,
            evaluando el PK solicitado original vs el PK realmente usado.
            """
            reasons = set()

            # OUT_OF_RANGE: solicitado fuera de rango global
            if pk_req_km < gmn or pk_req_km > gmx:
                reasons.add(self.ADJ_OUT_OF_RANGE)

            # GAP_SNAP: solicitado no cubierto por ningún tramo individual y se permite snap
            if snap_on_gaps and (not is_pk_covered_by_any_geom(geoms, pk_req_km, m_unit=m_unit)):
                if abs(pk_used_km - pk_req_km) > 1e-12:
                    reasons.add(self.ADJ_GAP_SNAP)

            adjusted = 1 if reasons or (abs(pk_used_km - pk_req_km) > 1e-12) else 0
            return adjusted, ";".join(sorted(reasons))

        # 5) Procesar tabla
        for row in seg_src.getFeatures():
            if feedback.isCanceled():
                return {}

            done += 1
            if total:
                feedback.setProgress(int(done * 100 / total))

            rid_val = row[seg_route_field]
            rid = str(rid_val).strip() if rid_val is not None else ""
            if not rid:
                feedback.pushWarning(self.tr("Row {row_id}: empty ROUTE_ID -> skipped.").format(row_id=row.id()))
                n_crit += 1
                if gen_issues:
                    _add_issue("", "", "", "", 0, "", [], [self.ERR_NO_ROUTE])
                continue

            ev = ""
            if event_id_field:
                evv = row[event_id_field]
                if evv is not None:
                    ev = str(evv).strip()

            # Parse PKs
            try:
                pk1_raw = pk_to_km(row[seg_pk1_field])
                pk2_raw = pk_to_km(row[seg_pk2_field])
            except Exception:
                feedback.pushWarning(self.tr("Row {row_id} (route '{route}'): invalid chainage (PK) -> skipped.").format(row_id=row.id(), route=rid))
                n_crit += 1
                if gen_issues:
                    _add_issue(
                        rid,
                        str(row[seg_pk1_field]),
                        str(row[seg_pk2_field]),
                        ev,
                        0,
                        "",
                        [],
                        [self.ERR_PK_INVALID],
                    )
                continue

            # Guardar solicitados por extremo (INI/FI N) tal cual vienen
            pk_ini_req = pk1_raw
            pk_fin_req = pk2_raw

            # Para extracción trabajamos con rango normalizado
            pk_ini_req_n, pk_fin_req_n = normalize_pk_range(pk_ini_req, pk_fin_req)
            pk_ini_req_str = format_pk(pk_ini_req_n)
            pk_fin_req_str = format_pk(pk_fin_req_n)

            geoms_with_mn = route_index.get(rid)
            if not geoms_with_mn:
                feedback.pushWarning(self.tr("Row {row_id} (route '{route}'): ROUTE_ID not found.").format(row_id=row.id(), route=rid))
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pk_ini_req_str, pk_fin_req_str, ev, 0, "", [], [self.ERR_NO_ROUTE])
                continue

            geoms = [g for (_mn, g) in geoms_with_mn]

            gmn, gmx = global_m_range_km(geoms, m_unit=m_unit)
            if gmn is None or gmx is None:
                feedback.pushWarning(self.tr("Row {row_id} (route '{route}'): no valid M range.").format(row_id=row.id(), route=rid))
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pk_ini_req_str, pk_fin_req_str, ev, 0, "", [], [self.ERR_NO_M_RANGE])
                continue

            adjust_reasons = set()

            # Rango normalizado para operar
            pk_ini = pk_ini_req_n
            pk_fin = pk_fin_req_n

            # Ajuste por fuera de rango global
            if pk_ini < gmn:
                pk_ini = gmn
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)
            if pk_fin > gmx:
                pk_fin = gmx
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)

            # Ajuste por huecos: extremos no cubiertos por ningún tramo individual
            snapped_any = False

            if not is_pk_covered_by_any_geom(geoms, pk_ini, m_unit=m_unit):
                if snap_on_gaps:
                    near = nearest_available_m_km(geoms, pk_ini, m_unit=m_unit)
                    if near is not None:
                        pk_ini = near
                        snapped_any = True
                else:
                    n_crit += 1
                    feedback.pushWarning(self.tr("Row {row_id} (route '{route}'): PK_INI falls in a gap and snapping is disabled.").format(row_id=row.id(), route=rid))
                    if gen_issues:
                        _add_issue(rid, pk_ini_req_str, pk_fin_req_str, ev, 0, "", [], [self.ERR_NO_MATCH])
                    continue

            if not is_pk_covered_by_any_geom(geoms, pk_fin, m_unit=m_unit):
                if snap_on_gaps:
                    near = nearest_available_m_km(geoms, pk_fin, m_unit=m_unit)
                    if near is not None:
                        pk_fin = near
                        snapped_any = True
                else:
                    n_crit += 1
                    feedback.pushWarning(self.tr("Row {row_id} (route '{route}'): PK_FIN falls in a gap and snapping is disabled.").format(row_id=row.id(), route=rid))
                    if gen_issues:
                        _add_issue(rid, pk_ini_req_str, pk_fin_req_str, ev, 0, "", [], [self.ERR_NO_MATCH])
                    continue

            if snapped_any:
                adjust_reasons.add(self.ADJ_GAP_SNAP)

            pk_ini, pk_fin = normalize_pk_range(pk_ini, pk_fin)

            # Extraer segmento
            seg_geom, adj_ini, adj_fin, n_pieces, _clipped2 = extract_segment_from_route_geoms(
                geoms,
                pk_ini,
                pk_fin,
                line_src.wkbType(),
                m_unit=m_unit,
                tolerance_km=tolerance_km,
            )

            if not seg_geom or seg_geom.isEmpty():
                n_crit += 1
                feedback.pushWarning(self.tr("Row {row_id} (route '{route}'): could not extract segment.").format(row_id=row.id(), route=rid))
                if gen_issues:
                    adjusted = 1 if adjust_reasons else 0
                    _add_issue(
                        rid,
                        pk_ini_req_str,
                        pk_fin_req_str,
                        ev,
                        adjusted,
                        ";".join(sorted(adjust_reasons)),
                        [],
                        [self.ERR_NO_MATCH],
                    )
                continue

            warnings = []
            if n_pieces > 1:
                warnings.append(self.WARN_SEGMENT_SPLIT)
                n_warn += 1
                feedback.pushWarning(self.tr("Row {row_id} (route '{route}'): segment split into {n} pieces.").format(row_id=row.id(), route=rid, n=n_pieces))

            adjusted = 1 if adjust_reasons else 0
            adjust_reason = ";".join(sorted(adjust_reasons))
            if adjusted:
                n_adj += 1
                feedback.pushWarning(
                    self.tr(
                        "Row {row_id} (route '{route}'): chainage (PK) adjusted ({reason}). "
                        "[{pk_ini_req}-{pk_fin_req}] -> [{pk_ini}-{pk_fin}]."
                    ).format(
                        row_id=row.id(),
                        route=rid,
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
            out_f["ROUTE_ID"] = rid
            if event_id_field:
                out_f["EVENT_ID"] = ev
            out_f["PK_INI"] = format_pk(adj_ini)
            out_f["PK_FIN"] = format_pk(adj_fin)
            out_f["DIST_PK_KM"] = dist_pk
            out_f["DIST_GEOM_KM"] = dist_geom
            out_f["ADJUSTED"] = adjusted
            out_f["ADJUST_REASON"] = adjust_reason
            out_f["N_PIECES"] = int(n_pieces)
            out_f["STATUS"] = "OK"

            if add_table_fields:
                for out_name, src_name in table_fields_map:
                    out_f[out_name] = row[src_name]

            line_sink.addFeature(out_f, QgsFeatureSink.FastInsert)
            added += 1
            n_ok += 1

            # Puntos extremos (2 puntos) con PK_REQ vs PK real y ajuste por extremo
            if pts_sink and pts_fields:
                ge = seg_geom.constGet()
                parts = [ge] if not seg_geom.isMultipart() else list(seg_geom.constParts())
                if parts:
                    v0 = list(parts[0].vertices())
                    vN = list(parts[-1].vertices())
                    if v0 and vN:
                        # INI (comparar contra PK solicitado normalizado)
                        ini_adj, ini_reason = _reasons_for_end(pk_ini_req_n, adj_ini, gmn, gmx, geoms)
                        pf = QgsFeature(pts_fields)
                        pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(v0[0].x(), v0[0].y())))
                        pf["ROUTE_ID"] = rid
                        if event_id_field:
                            pf["EVENT_ID"] = ev
                        pf["PK_REQ"] = format_pk(pk_ini_req_n)
                        pf["PK"] = format_pk(adj_ini)
                        pf["ADJUSTED"] = int(ini_adj)
                        pf["ADJUST_REASON"] = ini_reason
                        pts_sink.addFeature(pf, QgsFeatureSink.FastInsert)

                        # FIN (comparar contra PK solicitado normalizado)
                        fin_adj, fin_reason = _reasons_for_end(pk_fin_req_n, adj_fin, gmn, gmx, geoms)
                        pf = QgsFeature(pts_fields)
                        pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(vN[-1].x(), vN[-1].y())))
                        pf["ROUTE_ID"] = rid
                        if event_id_field:
                            pf["EVENT_ID"] = ev
                        pf["PK_REQ"] = format_pk(pk_fin_req_n)
                        pf["PK"] = format_pk(adj_fin)
                        pf["ADJUSTED"] = int(fin_adj)
                        pf["ADJUST_REASON"] = fin_reason
                        pts_sink.addFeature(pf, QgsFeatureSink.FastInsert)

            # Incidencias: registrar si hubo ajuste o warnings (no críticos)
            if gen_issues and (adjusted or warnings):
                _add_issue(rid, pk_ini_req_str, pk_fin_req_str, ev, adjusted, adjust_reason, warnings, [])

        if added == 0:
            raise QgsProcessingException(self.tr("No segments were generated (check routes, chainage (PK) and M calibration)."))

        result = {self.OUTPUT_LINES: line_id}
        if pts_id is not None:
            result[self.OUTPUT_POINTS] = pts_id

        feedback.pushInfo(self.tr("=== Summary ==="))
        feedback.pushInfo(self.tr("Segments OK: {n}").format(n=n_ok))
        feedback.pushInfo(self.tr("Adjusted (ADJUSTED): {n}").format(n=n_adj))
        feedback.pushInfo(f"Warnings: {n_warn}")
        feedback.pushInfo(self.tr("Criticals: {n}").format(n=n_crit))

        # Tabla incidencias
        if gen_issues and issues:
            feedback.pushInfo(self.tr("Issues table will be generated: {n} rows.").format(n=len(issues)))

            iss_fields = QgsFields()
            iss_fields.append(QgsField("ROUTE_ID", QVariant.String))
            if event_id_field:
                iss_fields.append(QgsField("EVENT_ID", QVariant.String))
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
                if event_id_field:
                    f["EVENT_ID"] = it["EVENT_ID"]
                f["PK_INI_REQ"] = it["PK_INI_REQ"]
                f["PK_FIN_REQ"] = it["PK_FIN_REQ"]
                f["ADJUSTED"] = it["ADJUSTED"]
                f["ADJUST_REASON"] = it["ADJUST_REASON"]
                f["WARNINGS"] = codes_to_str(it["WARNINGS"])
                f["CRITICALS"] = codes_to_str(it["CRITICALS"])
                sink_iss.addFeature(f, QgsFeatureSink.FastInsert)

            result[self.OUTPUT_ISSUES] = out_iss_id
        else:
            if gen_issues:
                feedback.pushInfo(self.tr("No issues: the issues table will not be generated."))

        return result