# -*- coding: utf-8 -*-
from __future__ import annotations

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
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsGeometry,
    QgsFeatureSink,
)

from .calib_utils import (
    adv,
    safe_str,
    geom_is_line,
    collect_m_values_strict,
    vertex_count,
    rebuild_geom_with_m,
)

# ---------------------------------------------------------
# Algorithm constants
# ---------------------------------------------------------

SCOPE_FEATURE = 0
SCOPE_ROUTE = 1

MONO_NONE = 0
MONO_INCREASING = 1
MONO_DECREASING = 2


def _enforce_monotonic(values: List[float], mode: int, eps: float) -> Tuple[List[float], bool]:
    """
    Enforces monotonicity on a list of numeric values.
    mode: 0 none, 1 increasing, 2 decreasing
    eps: small tolerance to allow minor numeric jitter.
    Returns (new_values, changed_flag).
    """
    if mode == MONO_NONE or not values:
        return values, False

    out = list(values)
    changed = False
    eps = float(eps)

    if mode == MONO_INCREASING:
        prev = out[0]
        for i in range(1, len(out)):
            if out[i] + eps < prev:
                out[i] = prev
                changed = True
            prev = out[i]
        return out, changed

    if mode == MONO_DECREASING:
        prev = out[0]
        for i in range(1, len(out)):
            if out[i] - eps > prev:
                out[i] = prev
                changed = True
            prev = out[i]
        return out, changed

    return out, False


def _is_monotonic_increasing(values: List[float], eps: float) -> bool:
    eps = float(eps)
    return all(values[i] + eps >= values[i - 1] for i in range(1, len(values)))


def _is_monotonic_decreasing(values: List[float], eps: float) -> bool:
    eps = float(eps)
    return all(values[i] - eps <= values[i - 1] for i in range(1, len(values)))


class ModifyMGeometry(QgsProcessingAlgorithm):
    INPUT_LINES = "INPUT_LINES"

    # scope
    SCOPE = "SCOPE"
    ROUTE_ID_LINES = "ROUTE_ID_LINES"

    # base transform
    OFFSET = "OFFSET"
    FACTOR = "FACTOR"

    # invert
    INVERT = "INVERT"

    # set start
    SET_START = "SET_START"
    TARGET_START = "TARGET_START"

    # clamp
    CLAMP = "CLAMP"
    CLAMP_MIN = "CLAMP_MIN"
    CLAMP_MAX = "CLAMP_MAX"

    # monotonic
    MONO_MODE = "MONO_MODE"
    MONO_EPS = "MONO_EPS"

    # validation
    REQUIRE_M = "REQUIRE_M"

    OUTPUT_LINES = "OUTPUT_LINES"

    # statuses/log types
    ST_OK = "OK"
    ST_BAD_GEOM = "BAD_GEOMETRY"
    ST_NO_M = "NO_M_VALUES"
    ST_NO_ROUTE = "NO_ROUTE"
    ST_SKIPPED_NO_M = "SKIPPED_NO_M"

    WARN_INVERT_APPLIED = "INVERT_APPLIED"
    WARN_SETSTART_APPLIED = "SET_START_APPLIED"
    WARN_CLAMP_APPLIED = "CLAMP_APPLIED"
    WARN_MONO_APPLIED = "MONO_APPLIED"
    WARN_NON_MONO_DETECTED = "NON_MONOTONIC_DETECTED"

    def name(self):
        return "modify_m_geometry"

    def displayName(self):
        return "Edit calibration (Modify M values)"

    def group(self):
        return "Calibrate M geometry"

    def groupId(self):
        return "calibrate_m_geometry"

    def createInstance(self):
        return ModifyMGeometry()

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("modify-m-geometry")

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)
    
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LINES,
                self.tr("Line layer (LineStringM/MultiLineStringM)"),
                [QgsProcessing.TypeVectorLine],
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterEnum(
                    self.SCOPE,
                    self.tr("Modification scope (for invert / set start)"),
                    options=[
                        self.tr("Per feature (recommended)"),
                        self.tr("Per ROUTE_ID (same range/start for multiple features)"),
                    ],
                    defaultValue=0,
                )
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterField(
                    self.ROUTE_ID_LINES,
                    self.tr("ROUTE_ID field in line layer (required if scope is Per ROUTE_ID)"),
                    parentLayerParameterName=self.INPUT_LINES,
                    type=QgsProcessingParameterField.Any,
                    optional=True,
                )
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.OFFSET,
                self.tr("Offset (add to M):  M' = M + offset"),
                QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.FACTOR,
                self.tr("Factor (multiply M):  M' = M * factor"),
                QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.INVERT,
                self.tr("Invert M while keeping the range (M' = (Mmin+Mmax) - M)"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SET_START,
                self.tr("Set the initial M to the target value (shift so the start equals TARGET_START)"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.TARGET_START,
                self.tr("TARGET_START (desired M at the start)"),
                QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CLAMP,
                self.tr("Clamp M to the range [min, max]"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.CLAMP_MIN,
                self.tr("Clamp min"),
                QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.CLAMP_MAX,
                self.tr("Clamp max"),
                QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterEnum(
                    self.MONO_MODE,
                    self.tr("Enforce monotonicity (remove backtracking)"),
                    options=[
                        "No",
                        self.tr("Enforce increasing (M[i] >= M[i-1])"),
                        self.tr("Enforce decreasing (M[i] <= M[i-1])"),
                    ],
                    defaultValue=0,
                )
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterNumber(
                    self.MONO_EPS,
                    self.tr("Monotonicity tolerance (epsilon)"),
                    QgsProcessingParameterNumber.Double,
                    defaultValue=0.0,
                )
            )
        )

        self.addParameter(
            adv(
                QgsProcessingParameterBoolean(
                    self.REQUIRE_M,
                    self.tr("Require all lines to have M (otherwise CRITICAL). If disabled, lines without M are skipped with WARNING"),
                    defaultValue=True,
                )
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LINES,
                self.tr("Lines (M modified)"),
            )
        )

    # ---------------------------------------------------------
    # Core ops
    # ---------------------------------------------------------

    @staticmethod
    def _apply_base_transform(ms: List[float], factor: float, offset: float) -> List[float]:
        return [float(m) * float(factor) + float(offset) for m in ms]

    @staticmethod
    def _apply_invert(ms: List[float], mmin: float, mmax: float) -> List[float]:
        s = float(mmin + mmax)
        return [s - float(m) for m in ms]

    @staticmethod
    def _apply_set_start_delta(ms: List[float], delta: float) -> List[float]:
        d = float(delta)
        return [float(m) + d for m in ms]

    @staticmethod
    def _apply_clamp(ms: List[float], cmin: float, cmax: float) -> Tuple[List[float], bool]:
        lo = min(float(cmin), float(cmax))
        hi = max(float(cmin), float(cmax))
        changed = False
        out: List[float] = []
        for m in ms:
            mm = float(m)
            if mm < lo:
                mm = lo
                changed = True
            elif mm > hi:
                mm = hi
                changed = True
            out.append(mm)
        return out, changed

    def processAlgorithm(self, parameters, context, feedback):
        src = self.parameterAsSource(parameters, self.INPUT_LINES, context)
        if src is None:
            raise QgsProcessingException(self.tr("Invalid line layer."))

        scope = int(self.parameterAsEnum(parameters, self.SCOPE, context))
        route_field = self.parameterAsString(parameters, self.ROUTE_ID_LINES, context) or None

        offset = float(self.parameterAsDouble(parameters, self.OFFSET, context))
        factor = float(self.parameterAsDouble(parameters, self.FACTOR, context))

        invert = self.parameterAsBoolean(parameters, self.INVERT, context)
        set_start = self.parameterAsBoolean(parameters, self.SET_START, context)
        target_start = float(self.parameterAsDouble(parameters, self.TARGET_START, context))

        clamp = self.parameterAsBoolean(parameters, self.CLAMP, context)
        clamp_min = float(self.parameterAsDouble(parameters, self.CLAMP_MIN, context))
        clamp_max = float(self.parameterAsDouble(parameters, self.CLAMP_MAX, context))

        mono_mode = int(self.parameterAsEnum(parameters, self.MONO_MODE, context))
        mono_eps = float(self.parameterAsDouble(parameters, self.MONO_EPS, context))

        require_m = self.parameterAsBoolean(parameters, self.REQUIRE_M, context)

        if scope == SCOPE_ROUTE and not route_field:
            raise QgsProcessingException(
                self.tr("Modification scope: 'Per ROUTE_ID' ERROR. you must specify the ROUTE_ID field in the line layer.")
            )

        # Output fields: keep originals + STATUS
        out_fields = QgsFields()
        for f in src.fields():
            out_fields.append(f)
        out_fields.append(QgsField("STATUS", QVariant.String))

        (sink, sink_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT_LINES,
            context,
            out_fields,
            QgsWkbTypes.addM(src.wkbType()),
            src.sourceCrs(),
        )

        feats: List[QgsFeature] = [f for f in src.getFeatures()]
        total = len(feats) or 0

        # Grouping for scope operations (invert / set_start rely on mmin/mmax and start reference)
        groups: Dict[str, List[QgsFeature]] = {}
        if scope == SCOPE_ROUTE:
            for f in feats:
                rid = safe_str(f[route_field])
                groups.setdefault(rid, []).append(f)
        else:
            for f in feats:
                groups[str(f.id())] = [f]

        # Precompute per-group stats AFTER base transform, BEFORE invert/setstart/clamp/mono.
        # group_stats: (mmin, mmax, gstart)
        group_stats: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = {}

        for gkey, flist in groups.items():
            if scope == SCOPE_ROUTE and not gkey:
                group_stats[gkey] = (None, None, None)
                continue

            g_mmin: Optional[float] = None
            g_mmax: Optional[float] = None
            g_start: Optional[float] = None

            for ff in flist:
                gg = ff.geometry()
                if gg is None or gg.isEmpty() or (not geom_is_line(gg)):
                    continue

                ms0 = collect_m_values_strict(gg)
                if ms0 is None or not ms0:
                    continue

                ms1 = self._apply_base_transform(ms0, factor, offset)

                if g_start is None:
                    g_start = float(ms1[0])

                local_min = min(ms1)
                local_max = max(ms1)
                g_mmin = local_min if g_mmin is None else min(g_mmin, local_min)
                g_mmax = local_max if g_mmax is None else max(g_mmax, local_max)

            group_stats[gkey] = (g_mmin, g_mmax, g_start)

        # Counters
        n_ok = 0
        n_warn = 0
        n_crit = 0
        warn_counts: Dict[str, int] = {}
        crit_counts: Dict[str, int] = {}

        def warn(tag: str, msg: str):
            nonlocal n_warn
            n_warn += 1
            warn_counts[tag] = warn_counts.get(tag, 0) + 1
            feedback.pushWarning(msg)

        def crit(tag: str, msg: str):
            nonlocal n_crit
            n_crit += 1
            crit_counts[tag] = crit_counts.get(tag, 0) + 1
            feedback.reportError(msg, fatalError=False)

        # Process features
        for i, f in enumerate(feats):
            if feedback.isCanceled():
                break
            if total:
                feedback.setProgress(int(100 * i / total))

            fid = str(f.id())
            geom = f.geometry()

            # Determine group key
            gkey = safe_str(f[route_field]) if scope == SCOPE_ROUTE else fid

            # Validate route key if needed
            if scope == SCOPE_ROUTE and not gkey:
                status = self.ST_NO_ROUTE
                crit(status, f"[Modify M] FID={fid} -> CRITICAL={status}")
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(list(f.attributes()) + [status])
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            # Validate geometry
            if geom is None or geom.isEmpty() or (not geom_is_line(geom)):
                status = self.ST_BAD_GEOM
                crit(status, f"[Modify M] FID={fid} ROUTE={gkey} -> CRITICAL={status}")
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(list(f.attributes()) + [status])
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            # Collect M (strict)
            ms0 = collect_m_values_strict(geom)
            if ms0 is None or not ms0:
                if require_m:
                    status = self.ST_NO_M
                    crit(status, f"[Modify M] FID={fid} ROUTE={gkey} -> CRITICAL={status}")
                else:
                    status = self.ST_SKIPPED_NO_M
                    warn(status, f"[Modify M] FID={fid} ROUTE={gkey} -> WARNING={status}")
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(list(f.attributes()) + [status])
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            # Base transform
            ms = self._apply_base_transform(ms0, factor, offset)

            # Non-monotonic detection (informative warning) when not enforcing
            if mono_mode == MONO_NONE and len(ms) >= 3:
                inc = _is_monotonic_increasing(ms, mono_eps)
                dec = _is_monotonic_decreasing(ms, mono_eps)
                if not (inc or dec):
                    warn(
                        self.WARN_NON_MONO_DETECTED,
                        f"[Modify M] FID={fid} ROUTE={gkey} -> WARNING={self.WARN_NON_MONO_DETECTED}",
                    )

            # Scope stats for invert/set_start
            mmin, mmax, gstart = group_stats.get(gkey, (None, None, None))

            # Invert (around scope range)
            if invert:
                if mmin is None or mmax is None:
                    status = self.ST_NO_M
                    crit(status, f"[Modify M] FID={fid} ROUTE={gkey} -> CRITICAL={status} (no range for invert)")
                    out_f = QgsFeature(out_fields)
                    out_f.setGeometry(geom)
                    out_f.setAttributes(list(f.attributes()) + [status])
                    sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                    continue

                ms = self._apply_invert(ms, mmin, mmax)
                warn(self.WARN_INVERT_APPLIED, f"[Modify M] FID={fid} ROUTE={gkey} -> WARNING={self.WARN_INVERT_APPLIED}")

            # Set start (shift so that start M matches TARGET_START)
            if set_start:
                if scope == SCOPE_FEATURE:
                    current_start = float(ms[0])
                    delta = float(target_start) - current_start
                else:
                    # Route scope: anchor against group start (after base transform),
                    # and if invert is active, invert that anchor consistently.
                    if gstart is not None:
                        if invert and (mmin is not None and mmax is not None):
                            gstart_eff = (float(mmin) + float(mmax)) - float(gstart)
                        else:
                            gstart_eff = float(gstart)
                        delta = float(target_start) - gstart_eff
                    else:
                        # fallback
                        delta = float(target_start) - float(ms[0])

                ms = self._apply_set_start_delta(ms, delta)
                warn(
                    self.WARN_SETSTART_APPLIED,
                    f"[Modify M] FID={fid} ROUTE={gkey} -> WARNING={self.WARN_SETSTART_APPLIED}",
                )

            # Clamp
            if clamp:
                ms, changed = self._apply_clamp(ms, clamp_min, clamp_max)
                if changed:
                    warn(self.WARN_CLAMP_APPLIED, f"[Modify M] FID={fid} ROUTE={gkey} -> WARNING={self.WARN_CLAMP_APPLIED}")

            # Enforce monotonic
            if mono_mode != MONO_NONE:
                ms2, changed = _enforce_monotonic(ms, mono_mode, mono_eps)
                ms = ms2
                if changed:
                    warn(self.WARN_MONO_APPLIED, f"[Modify M] FID={fid} ROUTE={gkey} -> WARNING={self.WARN_MONO_APPLIED}")

            # Rebuild geometry with new M
            vcount = vertex_count(geom)
            if vcount != len(ms):
                status = self.ST_BAD_GEOM
                crit(status, f"[Modify M] FID={fid} ROUTE={gkey} -> CRITICAL={status} (vertex mismatch)")
                out_f = QgsFeature(out_fields)
                out_f.setGeometry(geom)
                out_f.setAttributes(list(f.attributes()) + [status])
                sink.addFeature(out_f, QgsFeatureSink.FastInsert)
                continue

            out_geom = rebuild_geom_with_m(geom, ms)

            status = self.ST_OK
            out_f = QgsFeature(out_fields)
            out_f.setGeometry(out_geom)
            out_f.setAttributes(list(f.attributes()) + [status])
            sink.addFeature(out_f, QgsFeatureSink.FastInsert)
            n_ok += 1

        # Summary
        feedback.pushInfo(f"[Modify M] OK={n_ok}  WARNINGS={n_warn}  CRITICALS={n_crit}")
        if warn_counts:
            parts = [f"{k}:{v}" for k, v in sorted(warn_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning("[Modify M] " + self.tr("Warnings by type: ") + ", ".join(parts))
        if crit_counts:
            parts = [f"{k}:{v}" for k, v in sorted(crit_counts.items(), key=lambda kv: kv[1], reverse=True)]
            feedback.pushWarning("[Modify M] " + self.tr("Criticals by type: ") + ", ".join(parts))

        return {self.OUTPUT_LINES: sink_id}
