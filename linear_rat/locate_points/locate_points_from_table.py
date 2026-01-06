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


class LocatePointsFromTable(QgsProcessingAlgorithm):
    """
    Locate points from table

    Events table: each row defines one point to locate using ROUTE_ID + PK.
    Returns one point per valid row, interpolated over LineStringM geometries.
    """

    INPUT_LINES = "INPUT_LINES"
    ROUTE_ID_FIELD = "ROUTE_ID_FIELD"

    PK_TABLE = "PK_TABLE"
    PK_ROUTE_FIELD = "PK_ROUTE_FIELD"
    PK_VALUE_FIELD = "PK_VALUE_FIELD"
    EVENT_ID_FIELD = "EVENT_ID_FIELD"
    ADD_TABLE_FIELDS = "ADD_TABLE_FIELDS"

    M_UNITS = "M_UNITS"
    TOLERANCE_KM = "TOLERANCE_KM"

    SNAP_TO_NEAREST_AVAILABLE = "SNAP_TO_NEAREST_AVAILABLE"

    GENERATE_ISSUES = "GENERATE_ISSUES"
    OUTPUT_POINTS = "OUTPUT_POINTS"
    OUTPUT_ISSUES = "OUTPUT_ISSUES"

    # Ajuste unificado (reasons)
    ADJ_OUT_OF_RANGE = "OUT_OF_RANGE"
    ADJ_GAP_SNAP = "GAP_SNAP"

    # Criticals
    ERR_NO_ROUTE = "NO_ROUTE"
    ERR_PK_INVALID = "PK_INVALID"
    ERR_NO_M_RANGE = "NO_M_RANGE"
    ERR_NO_MATCH = "NO_MATCH"

    def name(self):
        return "locate_points_from_table"

    def displayName(self):
        return "Locate points from table"

    def group(self):
        return "Locate points (requires M geometry)"

    def groupId(self):
        return "locate_points"

    def createInstance(self):
        return LocatePointsFromTable()
    
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

        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.PK_TABLE,
                self.tr("Events/points table"),
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
                self.PK_VALUE_FIELD,
                self.tr("Chainage (PK) field in the table [km+mmm] or decimal number (km)"),
                parentLayerParameterName=self.PK_TABLE,
                type=QgsProcessingParameterField.Any,
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.EVENT_ID_FIELD,
                self.tr("Additional Point ID field (PK_ID)"),
                parentLayerParameterName=self.PK_TABLE,
                type=QgsProcessingParameterField.Any,
                optional=True,
            )
        )

        # UX: same text/position as the segments algorithm
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_TABLE_FIELDS,
                self.tr("Append table fields to the output"),
                defaultValue=False,
            )
        )

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
                    self.tr("Snap to the nearest available chainage (PK) when the geometry is incomplete"),
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
        return short_help("locate_points_from_table")

    def processAlgorithm(self, parameters, context, feedback):
        line_src = self.parameterAsSource(parameters, self.INPUT_LINES, context)
        if not line_src:
            raise QgsProcessingException(self.tr("Could not read the line layer."))
        if not QgsWkbTypes.hasM(line_src.wkbType()):
            raise QgsProcessingException(self.tr("The layer has no M values (it is not calibrated)."))

        pk_src = self.parameterAsSource(parameters, self.PK_TABLE, context)
        if not pk_src:
            raise QgsProcessingException(self.tr("Could not read the events table."))

        route_id_field = self.parameterAsFields(parameters, self.ROUTE_ID_FIELD, context)[0]
        pk_route_field = self.parameterAsFields(parameters, self.PK_ROUTE_FIELD, context)[0]
        pk_value_field = self.parameterAsFields(parameters, self.PK_VALUE_FIELD, context)[0]

        ev_fields = self.parameterAsFields(parameters, self.EVENT_ID_FIELD, context)
        event_id_field = ev_fields[0] if ev_fields else None

        add_table_fields = self.parameterAsBool(parameters, self.ADD_TABLE_FIELDS, context)

        m_units_idx = self.parameterAsEnum(parameters, self.M_UNITS, context)
        m_unit = "m" if m_units_idx == 0 else "km"

        tolerance_km = float(self.parameterAsDouble(parameters, self.TOLERANCE_KM, context) or 0.0)
        snap_on_gaps = self.parameterAsBool(parameters, self.SNAP_TO_NEAREST_AVAILABLE, context)
        gen_issues = self.parameterAsBool(parameters, self.GENERATE_ISSUES, context)

        # Indexar capa lineal por ROUTE_ID
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

        # Helper: append fields while handling collisions
        def _safe_append_field(dst_fields: QgsFields, f: QgsField, suffix: str = "_TBL") -> str:
            name = f.name()
            if dst_fields.indexFromName(name) != -1:
                name = f"{name}{suffix}"
            nf = QgsField(f)
            nf.setName(name)
            dst_fields.append(nf)
            return name

        # Salida puntos
        out_fields = QgsFields()
        out_fields.append(QgsField("ROUTE_ID", QVariant.String))
        if event_id_field:
            out_fields.append(QgsField("PK_ID", QVariant.String))
        out_fields.append(QgsField("PK_REQ", QVariant.String))
        out_fields.append(QgsField("PK", QVariant.String))
        out_fields.append(QgsField("ADJUSTED", QVariant.Int))
        out_fields.append(QgsField("ADJUST_REASON", QVariant.String))
        out_fields.append(QgsField("STATUS", QVariant.String))

        # Append table fields to the output (optional)
        tbl_field_map: list[tuple[int, str]] = []  # (src_index, out_name)
        if add_table_fields:
            src_fields = pk_src.fields()
            for i in range(src_fields.count()):
                f = src_fields.at(i)
                out_name = _safe_append_field(out_fields, f, suffix="_TBL")
                tbl_field_map.append((i, out_name))

        sink_pts, out_pts_id = self.parameterAsSink(
            parameters,
            self.OUTPUT_POINTS,
            context,
            out_fields,
            QgsWkbTypes.Point,
            line_src.sourceCrs(),
        )

        # Issues
        issues = []

        def _add_issue(
            route_id: str,
            pk_req: str,
            ev: str,
            adjusted: int,
            adjust_reason: str,
            warnings: list[str],
            criticals: list[str],
        ):
            issues.append(
                {
                    "ROUTE_ID": route_id,
                    "PK_REQ": pk_req,
                    "PK_ID": ev,
                    "ADJUSTED": adjusted,
                    "ADJUST_REASON": adjust_reason,
                    "WARNINGS": warnings[:],
                    "CRITICALS": criticals[:],
                }
            )

        n_ok = 0
        n_adj = 0
        n_crit = 0
        added = 0

        total_rows = pk_src.featureCount() if pk_src.featureCount() >= 0 else 0
        irow = 0

        for row in pk_src.getFeatures():
            if feedback.isCanceled():
                return {}

            irow += 1
            if total_rows:
                feedback.setProgress(int(irow * 100 / total_rows))

            rid_val = row[pk_route_field]
            pk_val = row[pk_value_field]
            if rid_val is None or pk_val is None:
                continue

            rid = str(rid_val).strip()
            if not rid:
                continue

            ev = ""
            if event_id_field:
                evv = row[event_id_field]
                if evv is not None:
                    ev = str(evv).strip()

            # Parse PK
            try:
                pk_req_km = pk_to_km(pk_val)
            except Exception:
                pk_req_str = str(pk_val)
                feedback.pushWarning(self.tr("Row {row}: invalid chainage (PK) ({pk}).").format(row=row.id(), pk=pk_val))
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pk_req_str, ev, 0, "", [], [self.ERR_PK_INVALID])
                continue

            pk_req_str = format_pk(pk_req_km)

            geoms = route_index.get(rid)
            if not geoms:
                feedback.pushWarning(self.tr("Row {row}: ROUTE_ID '{route}' not found.").format(row=row.id(), route=rid))
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pk_req_str, ev, 0, "", [], [self.ERR_NO_ROUTE])
                continue

            gmn, gmx = global_m_range_km(geoms, m_unit=m_unit)
            if gmn is None or gmx is None:
                feedback.pushWarning(self.tr("Row {row} (route '{route}'): no valid M range.").format(row=row.id(), route=rid))
                n_crit += 1
                if gen_issues:
                    _add_issue(rid, pk_req_str, ev, 0, "", [], [self.ERR_NO_M_RANGE])
                continue

            adjust_reasons = set()

            pk_real_km = pk_req_km
            if pk_real_km < gmn:
                pk_real_km = gmn
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)
            if pk_real_km > gmx:
                pk_real_km = gmx
                adjust_reasons.add(self.ADJ_OUT_OF_RANGE)

            def _locate(target_km: float):
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

            point_geom = None

            if is_pk_covered_by_any_geom(geoms, pk_real_km, m_unit=m_unit):
                point_geom = _locate(pk_real_km)
            else:
                # Hueco/discontinuidad
                if snap_on_gaps:
                    near = nearest_available_m_km(geoms, pk_real_km, m_unit=m_unit)
                    if near is not None:
                        pk_real_km = near
                        adjust_reasons.add(self.ADJ_GAP_SNAP)
                        point_geom = _locate(pk_real_km)
                else:
                    n_crit += 1
                    feedback.pushWarning(self.tr("Row {row} (route '{route}'): chainage (PK) is in a gap and snapping is disabled.").format(row=row.id(), route=rid))
                    if gen_issues:
                        _add_issue(rid, pk_req_str, ev, 0, "", [], [self.ERR_NO_MATCH])
                    continue

            if point_geom is None or point_geom.isEmpty():
                n_crit += 1
                feedback.pushWarning(self.tr("Row {row} (route '{route}'): could not locate point.").format(row=row.id(), route=rid))
                if gen_issues:
                    adjusted = 1 if adjust_reasons else 0
                    _add_issue(
                        rid,
                        pk_req_str,
                        ev,
                        adjusted,
                        ";".join(sorted(adjust_reasons)),
                        [],
                        [self.ERR_NO_MATCH],
                    )
                continue

            adjusted = 1 if adjust_reasons else 0
            adjust_reason = ";".join(sorted(adjust_reasons))
            if adjusted:
                n_adj += 1
                feedback.pushWarning(self.tr("Row {row} (route '{route}'): chainage (PK) adjusted ({reason}). {req} -> {real}.").format(row=row.id(), route=rid, reason=adjust_reason, req=pk_req_str, real=format_pk(pk_real_km)))

            out_f = QgsFeature(out_fields)
            out_f.setGeometry(point_geom)

            # Base fields
            out_f["ROUTE_ID"] = rid
            if event_id_field:
                out_f["PK_ID"] = ev
            out_f["PK_REQ"] = pk_req_str
            out_f["PK"] = format_pk(pk_real_km)
            out_f["ADJUSTED"] = adjusted
            out_f["ADJUST_REASON"] = adjust_reason
            out_f["STATUS"] = "OK"

            # Copy table fields (optional)
            if add_table_fields and tbl_field_map:
                for src_idx, out_name in tbl_field_map:
                    out_f[out_name] = row[src_idx]

            sink_pts.addFeature(out_f, QgsFeatureSink.FastInsert)

            added += 1
            n_ok += 1

            # Issues: record if there was an adjustment (warnings left empty here)
            if gen_issues and adjusted:
                _add_issue(rid, pk_req_str, ev, adjusted, adjust_reason, [], [])

        if added == 0:
            raise QgsProcessingException(self.tr("No points were generated (check routes, PK values and M calibration)."))

        result = {self.OUTPUT_POINTS: out_pts_id}

        feedback.pushInfo(self.tr("=== Summary ==="))
        feedback.pushInfo(self.tr("OK points: {n}").format(n=n_ok))
        feedback.pushInfo(self.tr("Adjusted (ADJUSTED): {n}").format(n=n_adj))
        feedback.pushInfo(self.tr("Criticals: {n}").format(n=n_crit))

        if gen_issues and issues:
            feedback.pushInfo(self.tr("An issues table will be generated: {n} rows.").format(n=len(issues)))

            iss_fields = QgsFields()
            iss_fields.append(QgsField("ROUTE_ID", QVariant.String))
            if event_id_field:
                iss_fields.append(QgsField("PK_ID", QVariant.String))
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
                if event_id_field:
                    f["PK_ID"] = it["PK_ID"]
                f["PK_REQ"] = it["PK_REQ"]
                f["ADJUSTED"] = it["ADJUSTED"]
                f["ADJUST_REASON"] = it["ADJUST_REASON"]
                f["WARNINGS"] = codes_to_str(it["WARNINGS"])
                f["CRITICALS"] = codes_to_str(it["CRITICALS"])
                sink_iss.addFeature(f, QgsFeatureSink.FastInsert)

            result[self.OUTPUT_ISSUES] = out_iss_id
        else:
            if gen_issues:
                feedback.pushInfo(self.tr("No issues: the issues table is not generated."))

        return result