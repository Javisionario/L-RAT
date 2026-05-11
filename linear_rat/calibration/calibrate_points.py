# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Dict, List

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterDistance,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsGeometry,
    QgsPointXY,
    QgsFeatureSink,
    QgsSpatialIndex,
)
from qgis.PyQt.QtCore import QVariant, QCoreApplication

from ..utils import format_pk
from .calib_utils import (
    adv,
    safe_str,
    has_any_m,
    geom_to_pointxy,
    closest_m_on_geom_fast,
)


@dataclass
class _BestMatch:
    dist_axis: float
    m_value: float
    route_id: str


class CalibratePoints(QgsProcessingAlgorithm):
    INPUT_POINTS = "INPUT_POINTS"
    INPUT_LINES = "INPUT_LINES"

    M_UNITS = "M_UNITS"
    MAX_DISTANCE = "MAX_DISTANCE"

    # UX
    RESTRICT_BY_ROUTE = "RESTRICT_BY_ROUTE"
    ADD_ROUTE_TO_OUTPUT = "ADD_ROUTE_TO_OUTPUT"

    ROUTE_ID_POINTS = "ROUTE_ID_POINTS"  # points field (optional, but required if RESTRICT)
    ROUTE_ID_LINES = "ROUTE_ID_LINES"    # lines field (optional, required if RESTRICT or ADD_ROUTE)

    GENERATE_ISSUES = "GENERATE_ISSUES"

    OUTPUT_POINTS = "OUTPUT_POINTS"
    OUTPUT_ISSUES = "OUTPUT_ISSUES"

    # Incidence types
    INC_NO_MATCH = "NO_MATCH"         # generic fallback
    INC_TOO_FAR = "TOO_FAR"           # nearest M projection exists but exceeds MAX_DISTANCE
    INC_NO_ROUTE = "NO_ROUTE"         # route missing/unknown when restrict enabled
    INC_NO_M = "NO_M_VALUES"          # no usable M segments found in candidates (or route)
    INC_BAD_GEOM = "BAD_GEOMETRY"     # invalid/empty/non-point

    def name(self):
        return "calibrate_points"

    def displayName(self):
        return "Calibrate points"

    def group(self):
        return "Calibrate M geometry"

    def groupId(self):
        return "calibrate_m_geometry"

    def createInstance(self):
        return CalibratePoints()

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_POINTS,
                self.tr("Point layer to calibrate"),
                [QgsProcessing.TypeVectorPoint],
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LINES,
                self.tr("Calibrated line layer (M)"),
                [QgsProcessing.TypeVectorLine],
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
            QgsProcessingParameterDistance(
                self.MAX_DISTANCE,
                self.tr("Maximum search / projection distance"),
                parentParameterName=self.INPUT_POINTS,
                defaultValue=50.0,
                minValue=0.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.RESTRICT_BY_ROUTE,
                self.tr("Restrict matching by ROUTE_ID (recommended if there are parallel routes)"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_ROUTE_TO_OUTPUT,
                self.tr("Add ROUTE_ID to output (from line layer)"),
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

        # Default: DISABLED (usually not useful here)
        self.addParameter(
            adv(
                QgsProcessingParameterBoolean(
                    self.GENERATE_ISSUES,
                    self.tr("Generate issues table (only if there are issues)"),
                    defaultValue=False,
                )
            )
        )

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_POINTS,
            self.tr("Calibrated points (with chainage/M)")
            )
        )

        # Must be optional: only created if the option is enabled and there are issues
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_ISSUES,
                self.tr("Issues (table)"),
                optional=True,
            )
        )

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("calibrate_points")

    # -------------------------
    # Helpers
    # -------------------------

    @staticmethod
    def _m_to_km(m_value: float, m_units: int) -> float:
        # 0=m, 1=km
        return float(m_value) / 1000.0 if m_units == 0 else float(m_value)

    @staticmethod
    def _append_unique_field(fields: QgsFields, preferred_name: str, field_type) -> str:
        """Append a generated field without overwriting existing input fields."""
        name = preferred_name
        if fields.indexFromName(name) != -1:
            base = f"{preferred_name}_LRAT"
            name = base
            i = 2
            while fields.indexFromName(name) != -1:
                name = f"{base}_{i}"
                i += 1
        fields.append(QgsField(name, field_type))
        return name

    # -------------------------
    # Execution
    # -------------------------

    def processAlgorithm(self, parameters, context, feedback):
        pts_src = self.parameterAsSource(parameters, self.INPUT_POINTS, context)
        line_src = self.parameterAsSource(parameters, self.INPUT_LINES, context)
        if pts_src is None:
            raise QgsProcessingException(self.tr("Invalid point layer."))
        if line_src is None:
            raise QgsProcessingException(self.tr("Invalid line layer."))

        m_units = int(self.parameterAsEnum(parameters, self.M_UNITS, context))
        max_dist = float(self.parameterAsDouble(parameters, self.MAX_DISTANCE, context))

        restrict = self.parameterAsBoolean(parameters, self.RESTRICT_BY_ROUTE, context)
        add_route = self.parameterAsBoolean(parameters, self.ADD_ROUTE_TO_OUTPUT, context)

        route_field_points = self.parameterAsString(parameters, self.ROUTE_ID_POINTS, context) or None
        route_field_lines = self.parameterAsString(parameters, self.ROUTE_ID_LINES, context) or None

        gen_issues = self.parameterAsBoolean(parameters, self.GENERATE_ISSUES, context)

        # UX validation
        if restrict and (not route_field_points or not route_field_lines):
            raise QgsProcessingException(
                self.tr("'Restrict matching by ROUTE_ID' requieres ROUTE_ID field for both points and lines layers.")
            )
        if add_route and (not route_field_lines):
            raise QgsProcessingException(
                self.tr("'Add ROUTE_ID to output' requieres ROUTE_ID field in the line layer.")
            )

        # Spatial index + line caches
        idx = QgsSpatialIndex()
        line_features: Dict[int, QgsFeature] = {}
        routes_in_lines = set() if route_field_lines else None
        any_m_found = False

        for lf in line_src.getFeatures():
            if feedback.isCanceled():
                break
            g = lf.geometry()
            if g is None or g.isEmpty():
                continue
            if not any_m_found and has_any_m(g):
                any_m_found = True

            fid = int(lf.id())
            line_features[fid] = lf
            idx.addFeature(lf)

            if routes_in_lines is not None:
                routes_in_lines.add(safe_str(lf[route_field_lines]))

        if not any_m_found:
            raise QgsProcessingException(
                self.tr("The line layer does not contain usable M values (LineStringM/MultiLineStringM).")
            )

        # Output fields (points)
        out_fields = QgsFields()
        for f in pts_src.fields():
            out_fields.append(f)

        # ROUTE_ID field copied from lines (optional)
        out_route_field_name = None
        if add_route:
            route_preferred = "ROUTE_ID"
            if out_fields.indexFromName(route_preferred) != -1:
                route_preferred = "ROUTE_ID_MATCH"
            out_route_field_name = self._append_unique_field(out_fields, route_preferred, QVariant.String)
        
        # Campos resultantes
        self._append_unique_field(out_fields, "PK", QVariant.String)          # km+mmm
        self._append_unique_field(out_fields, "M", QVariant.Double)           # raw M
        self._append_unique_field(out_fields, "DIST_AXIS", QVariant.Double)   # units of CRS
        self._append_unique_field(out_fields, "INCIDENCE", QVariant.Int)      # 0/1
        self._append_unique_field(out_fields, "INC_TYPE", QVariant.String)    # reason

        (sink, sink_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT_POINTS,
            context,
            out_fields,
            pts_src.wkbType(),
            pts_src.sourceCrs(),
        )

        # Accumulate issues in memory to decide whether to create the table
        issues_rows: List[Tuple[str, str, str, Optional[float], Optional[float], str]] = []

        # Contadores para resumen en log de Processing
        n_ok = 0
        n_inc = 0
        crit_counts: Dict[str, int] = {}

        total = pts_src.featureCount() or 0

        for i, pf in enumerate(pts_src.getFeatures()):
            if feedback.isCanceled():
                break
            if total:
                feedback.setProgress(int(100 * i / total))

            geom = pf.geometry()
            pt_id = str(pf.id())

            best: Optional[_BestMatch] = None
            inc_type = ""
            incidence = 0
            pk_txt = ""
            m_out: Optional[float] = None
            dist_out: Optional[float] = None
            route_match = ""

            pxy = geom_to_pointxy(geom)
            if pxy is None:
                incidence = 1
                inc_type = self.INC_BAD_GEOM
            else:
                # Route restriction (if applicable)
                route_filter = ""
                if restrict:
                    route_filter = safe_str(pf[route_field_points]) if route_field_points else ""
                    if not route_filter:
                        incidence = 1
                        inc_type = self.INC_NO_ROUTE
                    elif routes_in_lines is not None and route_filter not in routes_in_lines:
                        incidence = 1
                        inc_type = self.INC_NO_ROUTE

                if incidence == 0:
                    cand_ids = idx.nearestNeighbor(pxy, 12)

                    best_dist: Optional[float] = None
                    best_m: Optional[float] = None
                    best_route = ""

                    # Diagnostics
                    had_m_projection = False
                    min_dist_any: Optional[float] = None

                    for fid in cand_ids:
                        lf = line_features.get(fid)
                        if lf is None:
                            continue

                        # Route filter if enabled
                        if restrict:
                            try:
                                if safe_str(lf[route_field_lines]) != route_filter:
                                    continue
                            except Exception:
                                continue

                        lg = lf.geometry()
                        if lg is None or lg.isEmpty():
                            continue

                        # (dist_axis, m_interp)
                        res = closest_m_on_geom_fast(lg, pxy)
                        if res is None:
                            continue

                        dist_axis, m_val = res
                        had_m_projection = True

                        if min_dist_any is None or dist_axis < min_dist_any:
                            min_dist_any = dist_axis

                        # Aplicar umbral de distancia para el match final
                        if dist_axis > max_dist:
                            continue

                        if best_dist is None or dist_axis < best_dist:
                            best_dist = dist_axis
                            best_m = m_val
                            if route_field_lines:
                                try:
                                    best_route = safe_str(lf[route_field_lines])
                                except Exception:
                                    best_route = ""

                    if best_dist is None or best_m is None:
                        incidence = 1
                        if had_m_projection and min_dist_any is not None and min_dist_any > max_dist:
                            inc_type = self.INC_TOO_FAR
                            dist_out = float(min_dist_any)
                        elif not had_m_projection:
                            inc_type = self.INC_NO_M
                        else:
                            inc_type = self.INC_NO_MATCH
                    else:
                        best = _BestMatch(dist_axis=float(best_dist), m_value=float(best_m), route_id=best_route)

            # If OK, fill fields
            if best is not None and incidence == 0:
                route_match = best.route_id
                dist_out = best.dist_axis
                m_out = best.m_value
                pk_km = self._m_to_km(best.m_value, m_units)
                pk_txt = format_pk(pk_km)

            # Logging to the Processing log (feedback only)
            if incidence == 1:
                n_inc += 1
                crit_counts[inc_type] = crit_counts.get(inc_type, 0) + 1

                msg = f"[Calibrate points] PT_ID={pt_id}"
                if restrict and route_field_points:
                    try:
                        msg += f" ROUTE_ID={safe_str(pf[route_field_points])}"
                    except Exception:
                        pass
                if dist_out is not None:
                    msg += f" DIST_AXIS={float(dist_out):.3f}"
                msg += f" -> CRITICAL={inc_type}"

                feedback.reportError(msg, fatalError=False)
            else:
                n_ok += 1

            # Construir salida
            attrs = list(pf.attributes())
            if add_route:
                attrs.append(route_match)

            attrs.extend(
                [
                    pk_txt,
                    m_out,
                    dist_out,
                    int(incidence),
                    inc_type,
                ]
            )

            out_f = QgsFeature(out_fields)
            out_f.setGeometry(geom)
            out_f.setAttributes(attrs)
            sink.addFeature(out_f, QgsFeatureSink.FastInsert)

            # Store issue if applicable (only if option enabled)
            if gen_issues and incidence == 1:
                route_for_table = ""
                if restrict and route_field_points:
                    try:
                        route_for_table = safe_str(pf[route_field_points])
                    except Exception:
                        route_for_table = ""
                if not route_for_table:
                    route_for_table = route_match

                issues_rows.append((pt_id, route_for_table, pk_txt, m_out, dist_out, inc_type))

        results = {self.OUTPUT_POINTS: sink_id}

        # Create the table ONLY if there are issues (and option enabled)
        if gen_issues and issues_rows:
            issues_fields = QgsFields()
            issues_fields.append(QgsField("PT_ID", QVariant.String))
            issues_fields.append(QgsField("ROUTE_ID", QVariant.String))
            issues_fields.append(QgsField("PK", QVariant.String))
            issues_fields.append(QgsField("M", QVariant.Double))
            issues_fields.append(QgsField("DIST_AXIS", QVariant.Double))
            issues_fields.append(QgsField("INC_TYPE", QVariant.String))

            (issues_sink, issues_sink_id) = self.parameterAsSink(
                parameters,
                self.OUTPUT_ISSUES,
                context,
                issues_fields,
                QgsWkbTypes.NoGeometry,
                pts_src.sourceCrs(),
            )

            for (pt_id, route_id, pk, m_val, dist_val, inc_t) in issues_rows:
                feat = QgsFeature(issues_fields)
                feat.setAttributes([pt_id, route_id, pk, m_val, dist_val, inc_t])
                issues_sink.addFeature(feat, QgsFeatureSink.FastInsert)

            results[self.OUTPUT_ISSUES] = issues_sink_id

        # Resumen final en el log de Processing
        feedback.pushInfo(f"[Calibrate points] OK={n_ok}  INCIDENCES={n_inc}")
        if crit_counts:
            parts = [f"{k}:{v}" for k, v in sorted(crit_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning("[Calibrate points] " + self.tr("Issues by type: ") + ", ".join(parts))

        return results
