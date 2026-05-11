# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict

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
    ensure_multipart_line_geometry,
    global_m_range_km,
    is_pk_covered_by_any_geom,
    nearest_available_m_km,
    codes_to_str,
)


def _adv(p):
    """Marks a parameter as Advanced in Processing."""
    p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
    return p


class LocateSegmentsFromPKTable(QgsProcessingAlgorithm):
    """
    Extract segments from PK table

    Tabla de PKs: cada fila es un PK con ROUTE_ID + PAIR_ID.
    Para cada (ROUTE_ID, PAIR_ID):
      - Se ordenan los PK numéricamente
      - Se emparejan secuencialmente: (0,1), (2,3), ...
      - Si queda un PK suelto (n impar), se ignora (warning)

    Salida:
      - Segmentos (Line/MultiLineM) con ADJUSTED/ADJUST_REASON y N_PIECES/STATUS.
      - Puntos extremos opcionales (2 por segmento) con PK_REQ y PK real, y ajuste por extremo.
      - Tabla de incidencias opcional (solo si hay warnings/críticos).
    """

    INPUT_LINES = "INPUT_LINES"
    ROUTE_ID_FIELD = "ROUTE_ID_FIELD"

    PK_TABLE = "PK_TABLE"
    PK_ROUTE_FIELD = "PK_ROUTE_FIELD"
    PK_VALUE_FIELD = "PK_VALUE_FIELD"
    PAIR_ID_FIELD = "PAIR_ID_FIELD"

    ADD_TABLE_FIELDS = "ADD_TABLE_FIELDS"

    M_UNITS = "M_UNITS"
    TOLERANCE_KM = "TOLERANCE_KM"

    SNAP_TO_NEAREST_AVAILABLE = "SNAP_TO_NEAREST_AVAILABLE"
    GENERATE_ISSUES = "GENERATE_ISSUES"

    GEN_POINTS = "GEN_POINTS"

    OUTPUT_LINES = "OUTPUT_LINES"
    OUTPUT_POINTS = "OUTPUT_POINTS"
    OUTPUT_ISSUES = "OUTPUT_ISSUES"

    # Ajuste unificado
    ADJ_OUT_OF_RANGE = "OUT_OF_RANGE"
    ADJ_GAP_SNAP = "GAP_SNAP"

    # Warnings “reales” (no ajuste)
    WARN_SEGMENT_SPLIT = "SEGMENT_SPLIT"
    WARN_ODD_PK_IGNORED = "ODD_PK_IGNORED"

    # Críticos
    ERR_NO_ROUTE = "NO_ROUTE"
    ERR_PK_INVALID = "PK_INVALID"
    ERR_NO_M_RANGE = "NO_M_RANGE"
    ERR_NO_MATCH = "NO_MATCH"

    # -------------------------
    # Identidad / UI
    # -------------------------
    def name(self):
        return "locate_segments_from_pk_table"

    def displayName(self):
        return "Locate segments from points/events (PK) table"

    def group(self):
        return "Locate segments (requires M geometry)"

    def groupId(self):
        return "locate_segments"

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)

    def createInstance(self):
        return LocateSegmentsFromPKTable()

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("locate-segments-from-pk-table")

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
                self.PK_TABLE,
                self.tr("Events (PK) table (each row = 1 event(PK))"),
                [QgsProcessing.TypeVector],
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.PK_ROUTE_FIELD,
                self.tr("ROUTE_ID field in the table"),
                parentLayerParameterName=self.PK_TABLE,
                type=QgsProcessingParameterField.Any,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.PAIR_ID_FIELD,
                self.tr("PAIR_ID field (segment/event) in the table"),
                parentLayerParameterName=self.PK_TABLE,
                type=QgsProcessingParameterField.Any,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.PK_VALUE_FIELD,
                self.tr("Chainage (PK) field in the table [km+mmm] or decimal number (km)"),
                parentLayerParameterName=self.PK_TABLE,
                type=QgsProcessingParameterField.Any,
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
                    self.tr("Snap to the nearest available chainage (PK) point when geometry is incomplete"),
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

        pk_src = self.parameterAsSource(parameters, self.PK_TABLE, context)
        if not pk_src:
            raise QgsProcessingException(self.tr("Could not read the chainage (PK) table."))

        route_id_field = self.parameterAsFields(parameters, self.ROUTE_ID_FIELD, context)[0]
        pk_route_field = self.parameterAsFields(parameters, self.PK_ROUTE_FIELD, context)[0]
        pair_id_field = self.parameterAsFields(parameters, self.PAIR_ID_FIELD, context)[0]
        pk_value_field = self.parameterAsFields(parameters, self.PK_VALUE_FIELD, context)[0]

        add_table_fields = self.parameterAsBool(parameters, self.ADD_TABLE_FIELDS, context)

        m_units_idx = self.parameterAsEnum(parameters, self.M_UNITS, context)
        m_unit = "m" if m_units_idx == 0 else "km"

        tolerance_km = float(self.parameterAsDouble(parameters, self.TOLERANCE_KM, context) or 0.0)
        snap_on_gaps = self.parameterAsBool(parameters, self.SNAP_TO_NEAREST_AVAILABLE, context)
        gen_issues = self.parameterAsBool(parameters, self.GENERATE_ISSUES, context)
        make_points = self.parameterAsBool(parameters, self.GEN_POINTS, context)

        # -------------------------------------------------------
        # 1) Indexar la capa lineal por ROUTE_ID
        # -------------------------------------------------------
        feedback.pushInfo(self.tr("Indexing line layer by ROUTE_ID…"))
        route_index: dict[str, list[tuple[float, QgsGeometry]]] = {}

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

        # -------------------------------------------------------
        # 2) Leer tabla de PKs y agrupar por (ROUTE_ID, PAIR_ID)
        # -------------------------------------------------------
        feedback.pushInfo(self.tr("Grouping PKs by (ROUTE_ID, PAIR_ID)…"))
        groups: dict[tuple[str, str], list[tuple[float, QgsFeature]]] = defaultdict(list)

        issues = []

        def _add_issue(rid: str, pid: str, pk_ini_req: str, pk_fin_req: str,
                       adjusted: int, adjust_reason: str,
                       warnings: list[str], criticals: list[str]):
            issues.append({
                "ROUTE_ID": rid,
                "PAIR_ID": pid,
                "PK_INI_REQ": pk_ini_req,
                "PK_FIN_REQ": pk_fin_req,
                "ADJUSTED": adjusted,
                "ADJUST_REASON": adjust_reason,
                "WARNINGS": warnings[:],
                "CRITICALS": criticals[:],
            })

        total_rows = pk_src.featureCount() if pk_src.featureCount() >= 0 else 0
        read_count = 0

        for row in pk_src.getFeatures():
            if feedback.isCanceled():
                return {}

            read_count += 1
            if total_rows:
                feedback.setProgress(int(read_count * 30 / total_rows))

            rid_val = row[pk_route_field]
            pid_val = row[pair_id_field]
            pk_val = row[pk_value_field]

            if rid_val is None or pid_val is None or pk_val is None:
                continue

            rid = str(rid_val).strip()
            pid = str(pid_val).strip()
            if not rid or not pid:
                continue

            try:
                pk_km = pk_to_km(pk_val)
            except Exception:
                feedback.pushWarning(self.tr("Row {row_id} (route '{route}', pair '{pair}'): invalid chainage (PK) ({pk}).").format(row_id=row.id(), route=rid, pair=pid, pk=pk_val))
                if gen_issues:
                    _add_issue(rid, pid, str(pk_val), "", 0, "", [], [self.ERR_PK_INVALID])
                continue

            groups[(rid, pid)].append((pk_km, row))

        if not groups:
            raise QgsProcessingException(self.tr("No valid chainage (PK) values were found to generate segments."))

        # -------------------------------------------------------
        # 3) Campos de salida (líneas)
        # -------------------------------------------------------
        out_fields = QgsFields()
        out_fields.append(QgsField("ROUTE_ID", QVariant.String))
        out_fields.append(QgsField("PAIR_ID", QVariant.String))
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
            for fld in pk_src.fields():
                src = fld.name()
                out = f"T_{src}" if (src in reserved or out_fields.indexFromName(src) != -1) else src
                out_fields.append(QgsField(out, fld.type(), fld.typeName(), fld.length(), fld.precision()))
                table_fields_map.append((out, src))

        line_sink, line_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_LINES,
            context,
            out_fields,
            QgsWkbTypes.multiType(line_src.wkbType()),
            line_src.sourceCrs(),
        )

        # -------------------------------------------------------
        # 4) Puntos extremos (opcional) con PK_REQ y ajuste por extremo
        # -------------------------------------------------------
        pts_sink, pts_id, pts_fields = None, None, None
        if make_points:
            pts_fields = QgsFields()
            pts_fields.append(QgsField("ROUTE_ID", QVariant.String))
            pts_fields.append(QgsField("PAIR_ID", QVariant.String))
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

        def _reasons_for_end(pk_req_km: float, pk_used_km: float, gmn: float, gmx: float, geoms: list) -> tuple[int, str]:
            reasons = set()

            if pk_req_km < gmn or pk_req_km > gmx:
                reasons.add(self.ADJ_OUT_OF_RANGE)

            if snap_on_gaps and (not is_pk_covered_by_any_geom(geoms, pk_req_km, m_unit=m_unit)):
                if abs(pk_used_km - pk_req_km) > 1e-12:
                    reasons.add(self.ADJ_GAP_SNAP)

            adjusted = 1 if reasons or (abs(pk_used_km - pk_req_km) > 1e-12) else 0
            return adjusted, ";".join(sorted(reasons))

        # -------------------------------------------------------
        # 5) Generar segmentos por grupo (rid, pid)
        # -------------------------------------------------------
        total_groups = len(groups)
        added = 0
        gdone = 0

        n_ok = 0
        n_adj = 0
        n_warn = 0
        n_crit = 0

        for (rid, pid), pk_rows in groups.items():
            if feedback.isCanceled():
                return {}

            gdone += 1
            feedback.setProgress(30 + int(gdone * 70 / max(1, total_groups)))

            geoms_with_mn = route_index.get(rid)
            if not geoms_with_mn:
                msg = self.tr("Group (route '{route}', pair '{pair}'): ROUTE_ID not found in the line layer.").format(route=rid, pair=pid)
                feedback.pushWarning(msg)
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pid, "", "", 0, "", [], [self.ERR_NO_ROUTE])
                continue

            geoms = [g for (_mn, g) in geoms_with_mn]

            gmn, gmx = global_m_range_km(geoms, m_unit=m_unit)
            if gmn is None or gmx is None:
                msg = self.tr("Group (route '{route}', pair '{pair}'): no valid M range.").format(route=rid, pair=pid)
                feedback.pushWarning(msg)
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pid, "", "", 0, "", [], [self.ERR_NO_M_RANGE])
                continue

            pk_rows.sort(key=lambda t: t[0])
            pks = [pk for (pk, _row) in pk_rows]

            if len(pks) < 2:
                msg = self.tr("Group (route '{route}', pair '{pair}'): only {n} PK(s) -> cannot form a segment.").format(route=rid, pair=pid, n=len(pks))
                feedback.pushWarning(msg)
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pid, "", "", 0, "", [], [self.ERR_NO_MATCH])
                continue

            if len(pks) % 2 != 0:
                last_pk = pks[-1]
                feedback.pushWarning(
                    self.tr(
                        "Group (route '{route}', pair '{pair}'): odd number of PKs ({n}). "
                        "The last one is ignored ({pk})."
                    ).format(route=rid, pair=pid, n=len(pks), pk=format_pk(last_pk))
                )
                n_warn += 1
                if gen_issues:
                    _add_issue(rid, pid, format_pk(last_pk), "", 0, "", [self.WARN_ODD_PK_IGNORED], [])

            for i in range(0, len(pks) - 1, 2):
                # PK solicitados (ya vienen ordenados por el sort)
                pk_ini_req = pks[i]
                pk_fin_req = pks[i + 1]
                pk_ini_req_n, pk_fin_req_n = normalize_pk_range(pk_ini_req, pk_fin_req)

                pk_ini_req_str = format_pk(pk_ini_req_n)
                pk_fin_req_str = format_pk(pk_fin_req_n)

                adjust_reasons = set()

                # Operación sobre el rango normalizado
                pk_ini = pk_ini_req_n
                pk_fin = pk_fin_req_n

                # Ajuste por fuera de rango global
                if pk_ini < gmn:
                    pk_ini = gmn
                    adjust_reasons.add(self.ADJ_OUT_OF_RANGE)
                if pk_fin > gmx:
                    pk_fin = gmx
                    adjust_reasons.add(self.ADJ_OUT_OF_RANGE)

                # Ajuste por huecos (extremos no cubiertos por ningún tramo individual)
                snapped_any = False

                if not is_pk_covered_by_any_geom(geoms, pk_ini, m_unit=m_unit):
                    if snap_on_gaps:
                        near = nearest_available_m_km(geoms, pk_ini, m_unit=m_unit)
                        if near is not None:
                            pk_ini = near
                            snapped_any = True
                    else:
                        n_crit += 1
                        feedback.pushWarning(
                            self.tr("Group (route '{route}', pair '{pair}'): PK_INI falls in a gap and snapping is disabled.").format(route=rid, pair=pid)
                        )
                        if gen_issues:
                            _add_issue(rid, pid, pk_ini_req_str, pk_fin_req_str, 0, "", [], [self.ERR_NO_MATCH])
                        continue

                if not is_pk_covered_by_any_geom(geoms, pk_fin, m_unit=m_unit):
                    if snap_on_gaps:
                        near = nearest_available_m_km(geoms, pk_fin, m_unit=m_unit)
                        if near is not None:
                            pk_fin = near
                            snapped_any = True
                    else:
                        n_crit += 1
                        feedback.pushWarning(
                            self.tr("Group (route '{route}', pair '{pair}'): PK_FIN falls in a gap and snapping is disabled.").format(route=rid, pair=pid)
                        )
                        if gen_issues:
                            _add_issue(rid, pid, pk_ini_req_str, pk_fin_req_str, 0, "", [], [self.ERR_NO_MATCH])
                        continue

                if snapped_any:
                    adjust_reasons.add(self.ADJ_GAP_SNAP)

                pk_ini, pk_fin = normalize_pk_range(pk_ini, pk_fin)

                # Extraer
                seg_geom, adj_ini, adj_fin, n_pieces, _clipped_unused = extract_segment_from_route_geoms(
                    geoms,
                    pk_ini,
                    pk_fin,
                    line_src.wkbType(),
                    m_unit=m_unit,
                    tolerance_km=tolerance_km,
                )

                if not seg_geom or seg_geom.isEmpty():
                    n_crit += 1
                    feedback.pushWarning(
                        self.tr("Group (route '{route}', pair '{pair}'): could not extract segment [{pk_ini} - {pk_fin}].").format(route=rid, pair=pid, pk_ini=format_pk(pk_ini), pk_fin=format_pk(pk_fin))
                    )
                    if gen_issues:
                        adjusted = 1 if adjust_reasons else 0
                        _add_issue(
                            rid, pid, pk_ini_req_str, pk_fin_req_str,
                            adjusted, ";".join(sorted(adjust_reasons)),
                            [], [self.ERR_NO_MATCH]
                        )
                    continue

                warnings = []
                if n_pieces > 1:
                    warnings.append(self.WARN_SEGMENT_SPLIT)
                    n_warn += 1
                    feedback.pushWarning(
                        self.tr("Group (route '{route}', pair '{pair}'): segment split into {n} pieces.").format(route=rid, pair=pid, n=n_pieces)
                    )

                adjusted = 1 if adjust_reasons else 0
                adjust_reason = ";".join(sorted(adjust_reasons))
                if adjusted:
                    n_adj += 1
                    feedback.pushWarning(
                        self.tr(
                            "Group (route '{route}', pair '{pair}'): chainage (PK) values adjusted ({reason}). "
                            "[{pk_ini_req}-{pk_fin_req}] -> [{pk_ini}-{pk_fin}]."
                        ).format(
                            route=rid,
                            pair=pid,
                            reason=adjust_reason,
                            pk_ini_req=pk_ini_req_str,
                            pk_fin_req=pk_fin_req_str,
                            pk_ini=format_pk(adj_ini),
                            pk_fin=format_pk(adj_fin),
                        )
                    )

                dist_pk = round3(pk_distance_km(adj_ini, adj_fin))
                dist_geom = round3(geom_length_km(seg_geom, line_src.sourceCrs(), context.project()))

                seg_geom = ensure_multipart_line_geometry(seg_geom)
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(seg_geom)
                out_f["ROUTE_ID"] = rid
                out_f["PAIR_ID"] = pid
                out_f["PK_INI"] = format_pk(adj_ini)
                out_f["PK_FIN"] = format_pk(adj_fin)
                out_f["DIST_PK_KM"] = dist_pk
                out_f["DIST_GEOM_KM"] = dist_geom
                out_f["ADJUSTED"] = adjusted
                out_f["ADJUST_REASON"] = adjust_reason
                out_f["N_PIECES"] = int(n_pieces)
                out_f["STATUS"] = "OK"

                if add_table_fields:
                    # Copia atributos desde la fila representativa (la del PK inicial del par)
                    rep_row = pk_rows[i][1]
                    for out_name, tf in table_fields_map:
                        out_f[out_name] = rep_row[tf]

                if not line_sink.addFeature(out_f, QgsFeatureSink.FastInsert):
                    raise QgsProcessingException(self.tr("Could not write the extracted segment to the output layer."))
                added += 1
                n_ok += 1

                # Puntos extremos (2 por segmento)
                if pts_sink and pts_fields:
                    ge = seg_geom.constGet()
                    parts = [ge] if not seg_geom.isMultipart() else list(seg_geom.constParts())
                    if parts:
                        v0 = list(parts[0].vertices())
                        vN = list(parts[-1].vertices())
                        if v0 and vN:
                            # INI (PK_REQ normalizado)
                            ini_adj, ini_reason = _reasons_for_end(pk_ini_req_n, adj_ini, gmn, gmx, geoms)
                            pf = QgsFeature(pts_fields)
                            pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(v0[0].x(), v0[0].y())))
                            pf["ROUTE_ID"] = rid
                            pf["PAIR_ID"] = pid
                            pf["PK_REQ"] = format_pk(pk_ini_req_n)
                            pf["PK"] = format_pk(adj_ini)
                            pf["ADJUSTED"] = int(ini_adj)
                            pf["ADJUST_REASON"] = ini_reason
                            pts_sink.addFeature(pf, QgsFeatureSink.FastInsert)

                            # FIN (PK_REQ normalizado)
                            fin_adj, fin_reason = _reasons_for_end(pk_fin_req_n, adj_fin, gmn, gmx, geoms)
                            pf = QgsFeature(pts_fields)
                            pf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(vN[-1].x(), vN[-1].y())))
                            pf["ROUTE_ID"] = rid
                            pf["PAIR_ID"] = pid
                            pf["PK_REQ"] = format_pk(pk_fin_req_n)
                            pf["PK"] = format_pk(adj_fin)
                            pf["ADJUSTED"] = int(fin_adj)
                            pf["ADJUST_REASON"] = fin_reason
                            pts_sink.addFeature(pf, QgsFeatureSink.FastInsert)

                # Incidencias: registrar si hubo ajuste o warnings (no críticos)
                if gen_issues and (adjusted or warnings):
                    _add_issue(rid, pid, pk_ini_req_str, pk_fin_req_str, adjusted, adjust_reason, warnings, [])

        if added == 0:
            raise QgsProcessingException(self.tr("No segments were generated."))

        feedback.pushInfo(self.tr("=== Summary ==="))
        feedback.pushInfo(self.tr("Segments OK: {n}").format(n=n_ok))
        feedback.pushInfo(self.tr("Adjusted (ADJUSTED): {n}").format(n=n_adj))
        feedback.pushInfo(f"Warnings: {n_warn}")
        feedback.pushInfo(self.tr("Criticals: {n}").format(n=n_crit))

        # -------------------------------------------------------
        # 6) Tabla incidencias (solo si procede)
        # -------------------------------------------------------
        result = {self.OUTPUT_LINES: line_id}
        if pts_id is not None:
            result[self.OUTPUT_POINTS] = pts_id

        if gen_issues and issues:
            feedback.pushInfo(self.tr("Issues table will be generated: {n} rows.").format(n=len(issues)))

            iss_fields = QgsFields()
            iss_fields.append(QgsField("ROUTE_ID", QVariant.String))
            iss_fields.append(QgsField("PAIR_ID", QVariant.String))
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
                f["PAIR_ID"] = it["PAIR_ID"]
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
