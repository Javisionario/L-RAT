# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, FixedLocator, FixedFormatter, FuncFormatter

from qgis.PyQt.QtCore import QCoreApplication, Qt, QLocale
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputHtml,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFolderDestination,
)
from qgis.PyQt.QtGui import QIcon, QPixmap, QPainter, QFont

# =============================================================================
# UTILIDADES
# =============================================================================

def formatear_pk(pk_km):
    """
    PK (km float) -> formato K+MMM (ej. 91.740 -> 91+740).
    Controla carry por redondeo.
    """
    pk_km = np.asarray(pk_km, dtype=float)
    km = np.floor(pk_km).astype(int)
    m = np.round((pk_km - km) * 1000).astype(int)

    carry = m // 1000
    km = km + carry
    m = m % 1000

    return np.array([f"{k}+{mm:03d}" for k, mm in zip(km, m)], dtype=object)


def determinar_salto_y(max_y, min_y):
    """Step agradable para la cota (m)."""
    rango = max_y - min_y
    if rango <= 100:
        return 10
    if rango <= 250:
        return 25
    if rango <= 500:
        return 50
    if rango <= 1000:
        return 100
    return 250


def elegir_step(rango, candidatos, target_labels=10):
    """Selecciona un paso agradable aproximando nº de etiquetas objetivo."""
    if rango <= 0:
        return candidatos[0]
    step_obj = rango / max(1, (target_labels - 1))
    for s in candidatos:
        if s >= step_obj:
            return s
    return candidatos[-1]


def determinar_salto_slope(slope: np.ndarray, target_labels: int = 9) -> float:
    """Step agradable para pendiente (%)."""
    s = np.asarray(slope, dtype=float)
    s = s[np.isfinite(s)]
    if s.size == 0:
        return 1.0
    r = float(np.max(s) - np.min(s))
    candidatos = [0.2, 0.5, 1, 2, 5, 10, 20]
    return float(elegir_step(r, candidatos, target_labels=target_labels))


def calcular_ylim_bonito(
    y,
    step,
    margen_frac=0.06,
    margen_min_steps=1,
    floor_if_data_above: Optional[float] = None,
):
    """
    Devuelve (ymin, ymax) alineados a 'step' con margen bonito.

    floor_if_data_above:
      - Si se proporciona (p.ej. 0.0) y el mínimo real de datos >= ese floor,
        entonces no permite que ymin_plot caiga por debajo del floor.
      - Si hay datos por debajo del floor (p.ej. elevaciones negativas),
        NO aplica el clamp.
    """
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if y.size == 0:
        step = 1.0 if step <= 0 else step
        return 0.0, step

    ymin_d = float(np.min(y))
    ymax_d = float(np.max(y))
    rango = ymax_d - ymin_d

    step = 1.0 if step <= 0 else float(step)

    if rango > 0:
        margen_steps = int(np.ceil((margen_frac * rango) / step))
    else:
        margen_steps = 0
    margen_steps = max(margen_min_steps, margen_steps)
    margen = margen_steps * step

    ymin = np.floor((ymin_d - margen) / step) * step
    ymax = np.ceil((ymax_d + margen) / step) * step

    # Clamp condicional (ej. no bajar de 0 si no hay negativos)
    if floor_if_data_above is not None and ymin_d >= floor_if_data_above and ymin < floor_if_data_above:
        ymin = float(floor_if_data_above)  # 0.0 no rompe el alineado
        if ymax <= ymin:
            ymax = ymin + step

    if ymax <= ymin:
        ymax = ymin + step

    return float(ymin), float(ymax)


def unique_mean(x_base, y_base):
    """Deduplica x, promediando y."""
    x = np.asarray(x_base, dtype=float)
    y = np.asarray(y_base, dtype=float)

    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]

    if x.size == 0:
        return x, y

    order = np.argsort(x)
    x = x[order]
    y = y[order]

    xu, inv = np.unique(x, return_inverse=True)
    y_sum = np.bincount(inv, weights=y)
    y_cnt = np.bincount(inv)
    yu = y_sum / np.maximum(y_cnt, 1)
    return xu, yu


def interp_extrap_pk_to_x(pk_new, pk_base, x_base):
    """
    PK(km) -> X(m) robusta:
      - deduplicación en PK (media)
      - monotonicidad X no decreciente
      - interpolación + extrapolación en extremos
    """
    pk_new = np.asarray(pk_new, dtype=float)
    pk_u, x_u = unique_mean(pk_base, x_base)

    if pk_u.size == 0:
        return np.full_like(pk_new, np.nan, dtype=float)
    if pk_u.size == 1:
        return np.full_like(pk_new, x_u[0], dtype=float)

    x_u = np.maximum.accumulate(x_u)
    x_new = np.interp(pk_new, pk_u, x_u)

    dx = np.diff(x_u)
    idx = np.where(dx > 1e-9)[0]

    if idx.size > 0:
        i0 = idx[0]
        denom0 = pk_u[i0 + 1] - pk_u[i0]
        m0 = 0.0 if denom0 == 0 else (x_u[i0 + 1] - x_u[i0]) / denom0
    else:
        m0 = 0.0

    lo = pk_new < pk_u[0]
    x_new[lo] = x_u[0] + (pk_new[lo] - pk_u[0]) * m0

    if idx.size > 0:
        i1 = idx[-1]
        denom1 = pk_u[i1 + 1] - pk_u[i1]
        m1 = 0.0 if denom1 == 0 else (x_u[i1 + 1] - x_u[i1]) / denom1
    else:
        m1 = 0.0

    hi = pk_new > pk_u[-1]
    x_new[hi] = x_u[-1] + (pk_new[hi] - pk_u[-1]) * m1

    return x_new


def construir_ticks(x_m, pk_km=None, modo="pk", target_labels=10):
    """
    Ticks y etiquetas para X:
      - modo="pk": etiquetas K+MMM (PK real)
      - modo="m" : etiquetas con m/km
    """
    x_m = np.asarray(x_m, dtype=float)

    if modo == "pk":
        pk_km = np.asarray(pk_km, dtype=float)

        pk_u, x_u = unique_mean(pk_km, x_m)
        vmin, vmax = np.nanmin(pk_u), np.nanmax(pk_u)
        rango = vmax - vmin

        candidatos = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50]  # km
        step = elegir_step(rango, candidatos, target_labels=target_labels)

        start = np.floor(vmin / step) * step
        end = np.ceil(vmax / step) * step
        ticks_pk = np.arange(start, end + 1e-12, step)

        tick_positions = interp_extrap_pk_to_x(ticks_pk, pk_u, x_u)
        tick_labels = formatear_pk(ticks_pk)
        return tick_positions, tick_labels

    if modo == "m":
        vmin, vmax = np.nanmin(x_m), np.nanmax(x_m)
        rango = vmax - vmin

        candidatos = [10, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000]
        step = elegir_step(rango, candidatos, target_labels=target_labels)

        start = np.floor(vmin / step) * step
        end = np.ceil(vmax / step) * step
        ticks_m = np.arange(start, end + 1e-12, step)

        usar_km = rango >= 2500
        if usar_km:
            ticks_km = ticks_m / 1000.0
            step_km = step / 1000.0
            if np.isclose(step_km, round(step_km), atol=1e-12):
                tick_labels = [f"{int(round(t))} km" for t in ticks_km]
            else:
                tick_labels = [f"{t:.1f} km" for t in ticks_km]
        else:
            tick_labels = [f"{int(t)} m" for t in ticks_m]

        return ticks_m, tick_labels

    raise ValueError("modo debe ser 'pk' o 'm'")


def aplicar_ticks(ax, tick_positions, tick_labels, x_data=None,
                 extend_left=True, extend_right=True, xpad_frac=0.02):
    """Aplica ticks fijos a X y ajusta límites para incluir extrapolación."""
    pos = np.asarray(tick_positions, dtype=float)
    lab = np.asarray(tick_labels, dtype=object)

    order = np.argsort(pos)
    pos, lab = pos[order], lab[order]
    if pos.size > 1:
        keep = np.ones(pos.size, dtype=bool)
        keep[1:] = np.abs(np.diff(pos)) > 1e-6
        pos, lab = pos[keep], lab[keep]

    ax.xaxis.set_major_locator(FixedLocator(pos))
    ax.xaxis.set_major_formatter(FixedFormatter(lab))

    ax.tick_params(axis="x", rotation=45, pad=10)
    for t in ax.get_xticklabels():
        t.set_ha("right")
        t.set_rotation_mode("anchor")
        t.set_clip_on(False)

    ax.minorticks_off()

    if x_data is None:
        xmin_d, xmax_d = np.nanmin(pos), np.nanmax(pos)
    else:
        xd = np.asarray(x_data, dtype=float)
        xmin_d, xmax_d = np.nanmin(xd), np.nanmax(xd)

    xmin = min(xmin_d, np.nanmin(pos)) if extend_left else xmin_d
    xmax = max(xmax_d, np.nanmax(pos)) if extend_right else xmax_d

    span = (xmax - xmin) if xmax > xmin else 1.0
    ax.set_xlim(xmin, xmax + xpad_frac * span)


def _unit_formatter(unit: str, decimals: int = 0):
    def _fmt(val, _pos):
        if val is None or not np.isfinite(val):
            return ""
        if decimals <= 0:
            s = f"{val:.0f}"
        else:
            s = f"{val:.{decimals}f}"
        return f"{s} {unit}".strip()
    return _fmt


# =============================================================================
# ALGORITMO QGIS
# =============================================================================

class ProfileSlopePlotsAlgorithm(QgsProcessingAlgorithm):

    def icon(self) -> QIcon:
        try:
            emoji = "📈"
            size = 64
            pix = QPixmap(size, size)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            font = QFont()
            font.setPointSize(int(size * 0.72))
            p.setFont(font)
            p.drawText(pix.rect(), Qt.AlignCenter, emoji)
            p.end()
            return QIcon(pix)
        except Exception:
            return QIcon()

    INPUT = "INPUT"
    ID_FIELD = "ID_FIELD"
    X_FIELD = "X_FIELD"
    Y_FIELD = "Y_FIELD"
    SLOPE_FIELD = "SLOPE_FIELD"
    USE_PK = "USE_PK"
    PK_FIELD = "PK_FIELD"
    SHOW_AXIS_LABELS = "SHOW_AXIS_LABELS"
    OUT_FOLDER = "OUT_FOLDER"
    OUT_REPORT = "OUT_REPORT"

    def name(self) -> str:
        return "profile_slope_plots"

    def displayName(self) -> str:
        return "Profile Slope Plotter"

    def group(self) -> str:
        return "Profile & Slope"

    def groupId(self) -> str:
        return "profile_slope"

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("profile-slope-plotter")

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)

    def initAlgorithm(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr("Input table/layer (can be without geometry)"),
                [QgsProcessing.TypeVector]
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.ID_FIELD,
                self.tr("ID field (one plot per ID)"),
                parentLayerParameterName=self.INPUT,
                optional=True,
                defaultValue="ID_Segmento"
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.X_FIELD,
                self.tr("X field (distance from origin, meters)"),
                parentLayerParameterName=self.INPUT,
                optional=False,
                defaultValue="Dist_Origen_metros"
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.Y_FIELD,
                self.tr("Y field (elevation / profile)"),
                parentLayerParameterName=self.INPUT,
                optional=False,
                defaultValue="Cota_SUAV"
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.SLOPE_FIELD,
                self.tr("Slope field (%)"),
                parentLayerParameterName=self.INPUT,
                optional=True,
                defaultValue="SLOPE"
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.USE_PK,
                self.tr("Use chainage (PK) on X axis (if available)"),
                defaultValue=False
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.PK_FIELD,
                self.tr("Chainage (PK) field"),
                parentLayerParameterName=self.INPUT,
                optional=True,
                defaultValue="m_field_PK_KM"
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SHOW_AXIS_LABELS,
                self.tr("Show plot labels (title and axis labels)"),
                defaultValue=True
            )
        )

        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUT_FOLDER,
                self.tr("Output folder (PNG + index.html)")
            )
        )

        self.addOutput(QgsProcessingOutputHtml(self.OUT_REPORT, self.tr("LRAT Profile Slope Plotter: HTML report")))

    def createInstance(self) -> "ProfileSlopePlotsAlgorithm":
        return ProfileSlopePlotsAlgorithm()

    @staticmethod
    def _to_float(v: Any) -> float:
        try:
            if v is None:
                return float("nan")
            return float(v)
        except Exception:
            return float("nan")

    @staticmethod
    def _safe_str(v: Any) -> str:
        return "" if v is None else str(v)

    def _find_plugin_icon(self) -> Optional[str]:
        """
        Busca icon.png subiendo desde la ruta de este archivo.
        Asume que icon.png está en la raíz del plugin.
        """
        cur = os.path.abspath(os.path.dirname(__file__))
        for _ in range(6):
            cand = os.path.join(cur, "icon.png")
            if os.path.exists(cand):
                return cand
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return None

    def _aggregate_by_id(self,
                         feats,
                         id_field: Optional[str],
                         x_field: str,
                         y_field: str,
                         slope_field: Optional[str],
                         pk_field: Optional[str],
                         feedback) -> Dict[str, Dict[str, np.ndarray]]:
        """Dict: id -> {"x","y","slope","pk"} agregando duplicados por X (media)."""
        data: Dict[str, Dict[float, Dict[str, List[float]]]] = {}

        for f in feats:
            if feedback.isCanceled():
                break

            fid = "ALL"
            if id_field:
                fid = self._safe_str(f[id_field]) or "ALL"

            x = self._to_float(f[x_field])
            y = self._to_float(f[y_field])
            if not np.isfinite(x) or not np.isfinite(y):
                continue

            slope = float("nan")
            if slope_field:
                slope = self._to_float(f[slope_field])

            pk = float("nan")
            if pk_field:
                pk = self._to_float(f[pk_field])

            if fid not in data:
                data[fid] = {}
            if x not in data[fid]:
                data[fid][x] = {"y": [], "slope": [], "pk": []}

            data[fid][x]["y"].append(y)
            data[fid][x]["slope"].append(slope)
            data[fid][x]["pk"].append(pk)

        out: Dict[str, Dict[str, np.ndarray]] = {}
        for fid, byx in data.items():
            xs = np.array(sorted(byx.keys()), dtype=float)
            ys = np.array([np.nanmean(byx[x]["y"]) for x in xs], dtype=float)
            sl = np.array([np.nanmean(byx[x]["slope"]) for x in xs], dtype=float)
            pk = np.array([np.nanmean(byx[x]["pk"]) for x in xs], dtype=float)
            out[fid] = {"x": xs, "y": ys, "slope": sl, "pk": pk}

        return out

    @staticmethod
    def _style_profile_axis(ax) -> None:
        ax.set_facecolor((0.7, 0.7, 0.7, 0.15))

    def _plot_profile(self,
                      x: np.ndarray,
                      y: np.ndarray,
                      modo_x: str,
                      tick_positions: np.ndarray,
                      tick_labels,
                      out_png: str,
                      show_axis_labels: bool) -> None:

        salto_y = determinar_salto_y(np.nanmax(y), np.nanmin(y))
        ymin_plot, ymax_plot = calcular_ylim_bonito(
            y, salto_y, margen_frac=0.06, margen_min_steps=1, floor_if_data_above=0.0
        )

        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        self._style_profile_axis(ax)

        ax.fill_between(
            x, y, y2=ymin_plot,
            where=np.isfinite(y),
            color="#1b5e20", alpha=0.8, interpolate=True
        )
        ax.plot(x, y, color="#0d3b12", linewidth=1)

        ax.set_ylim(ymin_plot, ymax_plot)
        ax.yaxis.set_major_locator(MultipleLocator(salto_y))
        ax.yaxis.set_major_formatter(FuncFormatter(_unit_formatter("m", decimals=0)))
        ax.grid(True, which="major", axis="both", alpha=0.25)

        aplicar_ticks(ax, tick_positions, tick_labels, x_data=x,
                      extend_left=True, extend_right=True, xpad_frac=0.02)

        if show_axis_labels:
            # Axis labels must remain in Spanish (fixed, not translatable)
            ax.set_ylabel("Cota (m)", labelpad=10)
            ax.set_xlabel("PK" if modo_x == "pk" else "Distancia")
            # Plot title can be translated
            ax.set_title(self.tr("Longitudinal profile"))
        else:
            ax.xaxis.label.set_visible(False)
            ax.yaxis.label.set_visible(False)
            ax.set_title("")

        fig.patch.set_alpha(0.0)
        plt.tight_layout()
        fig.savefig(out_png, transparent=True)
        plt.close(fig)

    def _plot_profile_plus_slope(self,
                                 x: np.ndarray,
                                 y: np.ndarray,
                                 slope: np.ndarray,
                                 modo_x: str,
                                 tick_positions: np.ndarray,
                                 tick_labels,
                                 out_png: str,
                                 show_axis_labels: bool) -> None:

        salto_y = determinar_salto_y(np.nanmax(y), np.nanmin(y))
        ymin_plot, ymax_plot = calcular_ylim_bonito(
            y, salto_y, margen_frac=0.06, margen_min_steps=1, floor_if_data_above=0.0
        )

        salto_s = determinar_salto_slope(slope, target_labels=9)
        smin_plot, smax_plot = calcular_ylim_bonito(
            slope, salto_s, margen_frac=0.10, margen_min_steps=1, floor_if_data_above=None
        )

        fig, ax1 = plt.subplots(figsize=(12, 6), dpi=150)
        self._style_profile_axis(ax1)

        ax1.fill_between(
            x, y, y2=ymin_plot,
            where=np.isfinite(y),
            color="#1b5e20", alpha=0.8, interpolate=True
        )
        ax1.plot(x, y, color="#0d3b12", linewidth=1)

        ax1.set_ylim(ymin_plot, ymax_plot)
        ax1.yaxis.set_major_locator(MultipleLocator(salto_y))
        ax1.yaxis.set_major_formatter(FuncFormatter(_unit_formatter("m", decimals=0)))
        ax1.grid(True, which="major", axis="both", alpha=0.25)

        aplicar_ticks(ax1, tick_positions, tick_labels, x_data=x,
                      extend_left=True, extend_right=True, xpad_frac=0.02)

        ax2 = ax1.twinx()
        ax2.plot(x, slope, color="black", linewidth=1.2, linestyle="--")

        ax2.set_ylim(smin_plot, smax_plot)
        ax2.yaxis.set_major_locator(MultipleLocator(salto_s))
        ax2.yaxis.set_major_formatter(FuncFormatter(_unit_formatter("%", decimals=1)))
        ax2.tick_params(axis="y", labelcolor="black")

        if show_axis_labels:
            # Axis labels must remain in Spanish (fixed, not translatable)
            ax1.set_ylabel("Cota (m)", labelpad=10)
            ax1.set_xlabel("PK" if modo_x == "pk" else "Distancia")
            ax2.set_ylabel("Pendiente (%)", color="black", labelpad=10)
            # Plot title can be translated
            ax1.set_title(self.tr("Longitudinal profile with slope (%)"))
        else:
            ax1.xaxis.label.set_visible(False)
            ax1.yaxis.label.set_visible(False)
            ax2.yaxis.label.set_visible(False)
            ax1.set_title("")

        fig.patch.set_alpha(0.0)
        plt.tight_layout()
        fig.savefig(out_png, transparent=True)
        plt.close(fig)

    def processAlgorithm(self, parameters: Dict[str, Any], context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Could not read the input table/layer."))

        id_field = (self.parameterAsString(parameters, self.ID_FIELD, context) or "").strip()
        x_field = (self.parameterAsString(parameters, self.X_FIELD, context) or "").strip()
        y_field = (self.parameterAsString(parameters, self.Y_FIELD, context) or "").strip()
        slope_field = (self.parameterAsString(parameters, self.SLOPE_FIELD, context) or "").strip()
        use_pk = self.parameterAsBool(parameters, self.USE_PK, context)
        pk_field = (self.parameterAsString(parameters, self.PK_FIELD, context) or "").strip()
        show_axis_labels = self.parameterAsBool(parameters, self.SHOW_AXIS_LABELS, context)
        out_folder = (self.parameterAsString(parameters, self.OUT_FOLDER, context) or "").strip()

        if not x_field or not y_field:
            raise QgsProcessingException(self.tr("You must set X_FIELD and Y_FIELD."))
        if not out_folder:
            raise QgsProcessingException(self.tr("You must set an output folder."))

        os.makedirs(out_folder, exist_ok=True)

        id_field = id_field or None
        slope_field = slope_field or None
        pk_field = pk_field or None
        if not use_pk:
            pk_field = None

        grouped = self._aggregate_by_id(
            feats=source.getFeatures(),
            id_field=id_field,
            x_field=x_field,
            y_field=y_field,
            slope_field=slope_field,
            pk_field=pk_field,
            feedback=feedback
        )

        if not grouped:
            raise QgsProcessingException(self.tr("No valid records found to plot."))

        rows = []

        for fid in sorted(grouped.keys()):
            if feedback.isCanceled():
                break

            x = grouped[fid]["x"]
            y = grouped[fid]["y"]
            slope = grouped[fid]["slope"]
            pk = grouped[fid]["pk"] if (use_pk and "pk" in grouped[fid]) else None

            safe_fid = fid.replace(os.sep, "_").replace(".", "_")
            out_png_profile = os.path.join(out_folder, f"perfil_{safe_fid}.png")
            out_png_profile_slope = os.path.join(out_folder, f"perfil_pendiente_{safe_fid}.png")

            if use_pk and pk is not None and np.any(np.isfinite(pk)):
                modo_x = "pk"
                tick_positions, tick_labels = construir_ticks(x, pk_km=pk, modo="pk", target_labels=10)
            else:
                modo_x = "m"
                tick_positions, tick_labels = construir_ticks(x, modo="m", target_labels=10)

            self._plot_profile_plus_slope(
                x=x,
                y=y,
                slope=slope,
                modo_x=modo_x,
                tick_positions=tick_positions,
                tick_labels=tick_labels,
                out_png=out_png_profile_slope,
                show_axis_labels=show_axis_labels
            )

            self._plot_profile(
                x=x,
                y=y,
                modo_x=modo_x,
                tick_positions=tick_positions,
                tick_labels=tick_labels,
                out_png=out_png_profile,
                show_axis_labels=show_axis_labels
            )

            rows.append((fid, os.path.basename(out_png_profile), os.path.basename(out_png_profile_slope)))

        # ---------------------------------------------------------------------
        # HTML sobrio + compatible con modo oscuro, pero con marco BLANCO para PNGs transparentes
        # ---------------------------------------------------------------------
        html_lang = (QLocale().name() or "en").replace("_", "-").lower()
        html_path = os.path.join(out_folder, "index.html")

        icon_href = None
        icon_src = self._find_plugin_icon()
        if icon_src:
            try:
                dst = os.path.join(out_folder, "icon.png")
                if not os.path.exists(dst):
                    shutil.copy2(icon_src, dst)
                icon_href = "icon.png"
            except Exception:
                icon_href = None

        # NOTE: The HTML report is NOT translated.
        title_txt = "L-RAT — Profile plotter report"
        h1_txt = "Longitudinal profile plotter"
        sub_txt = "Longitudinal profiles and slope curves by ID"

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f"<!doctype html>\n<html lang='{html_lang}'>\n<head>\n")
            f.write("<meta charset='utf-8'>\n")
            f.write("<meta name='viewport' content='width=device-width, initial-scale=1'>\n")
            f.write("<meta name='color-scheme' content='light dark'>\n")
            f.write(f"<title>{title_txt}</title>\n")
            if icon_href:
                f.write(f"<link rel='icon' href='{icon_href}'>\n")

            f.write("<style>\n")
            f.write(":root{--bg:#f6f7f9;--panel:#ffffff;--text:#111827;--muted:#6b7280;--border:#e5e7eb;--shadow:0 1px 2px rgba(0,0,0,.06);--radius:12px;}\n")
            f.write("@media (prefers-color-scheme: dark){:root{--bg:#0b1220;--panel:#0f172a;--text:#e5e7eb;--muted:#94a3b8;--border:#1f2937;--shadow:0 1px 2px rgba(0,0,0,.35);}}\n")
            f.write("html,body{height:100%;}\n")
            f.write("body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;}\n")
            f.write(".wrap{max-width:1100px;margin:0 auto;padding:22px;}\n")
            f.write("header{display:flex;align-items:center;gap:14px;padding:16px 18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow);}\n")
            f.write(".brand{display:flex;align-items:center;gap:12px;min-width:0;}\n")
            f.write(".brand img{width:34px;height:34px;border-radius:8px;}\n")
            f.write("h1{font-size:18px;line-height:1.2;margin:0;}\n")
            f.write(".sub{margin:3px 0 0 0;color:var(--muted);font-size:13px;}\n")
            f.write(".list{margin-top:18px;display:flex;flex-direction:column;gap:14px;}\n")
            f.write(".item{border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);box-shadow:var(--shadow);}\n")
            f.write(".item-h{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--border);}\n")
            f.write(".id{font-weight:650;}\n")
            f.write(".grid{display:grid;grid-template-columns:1fr;gap:12px;padding:12px 14px;}\n")
            f.write("@media(min-width:980px){.grid{grid-template-columns:1fr 1fr;}}\n")
            f.write(".card{border:1px solid var(--border);border-radius:12px;padding:12px;background:transparent;}\n")
            f.write(".card h2{font-size:14px;margin:0 0 8px 0;}\n")
            f.write(".links{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;}\n")
            f.write("a{color:inherit;text-decoration:none;}\n")
            f.write(".foot a{color:#2563eb;text-decoration:underline;text-underline-offset:2px;}\n")
            f.write(".foot a:hover{text-decoration-thickness:2px;}\n")
            f.write("@media (prefers-color-scheme: dark){.foot a{color:#60a5fa;}}\n")
            f.write(".btn{display:inline-flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.02);}\n")
            f.write(".btn:hover{background:rgba(148,163,184,.12);}\n")

            # ---- Contenedor SIEMPRE blanco para PNG transparentes ----
            # Borde fijo suave (no depende del modo oscuro)
            f.write(".plotbox{background:#ffffff;border:1px solid rgba(17,24,39,.18);border-radius:10px;padding:10px;}\n")
            f.write("img.plot{max-width:100%;height:auto;display:block;border-radius:6px;}\n")

            f.write(".foot{margin-top:16px;color:var(--muted);font-size:12px;}\n")
            f.write("</style>\n</head>\n<body>\n")

            f.write("<div class='wrap'>\n")
            f.write("<header>\n")
            f.write("<div class='brand'>\n")
            if icon_href:
                f.write(f"<img src='{icon_href}' alt='icon'>\n")
            f.write("<div>\n")
            f.write(f"<h1>{h1_txt}</h1>\n")
            f.write(f"<div class='sub'>{sub_txt}</div>\n")
            f.write("</div></div>\n")
            f.write("</header>\n")

            f.write("<div class='list'>\n")

            for fid, png_profile, png_profile_slope in rows:
                f.write("<section class='item'>\n")
                f.write("<div class='item-h'>\n")
                f.write(f"<div class='id'>ID: {fid}</div>\n")
                f.write("</div>\n")

                f.write("<div class='grid'>\n")

                # Card: Perfil
                f.write("<div class='card'>\n")
                f.write("<h2>Longitudinal profile</h2>\n")
                f.write("<div class='links'>\n")
                f.write(f"<a class='btn' href='{png_profile}' target='_blank' rel='noopener'>🖼️ Open</a>\n")
                f.write(f"<a class='btn' href='{png_profile}' download>⬇️ Download</a>\n")
                f.write("</div>\n")
                f.write("<div class='plotbox'>\n")
                f.write(f"<img class='plot' src='{png_profile}' alt='perfil_{fid}'>\n")
                f.write("</div>\n")
                f.write("</div>\n")

                # Card: Perfil longitudinal + pendiente
                f.write("<div class='card'>\n")
                f.write("<h2>Longitudinal profile + slope curve</h2>\n")
                f.write("<div class='links'>\n")
                f.write(f"<a class='btn' href='{png_profile_slope}' target='_blank' rel='noopener'>🖼️ Open</a>\n")
                f.write(f"<a class='btn' href='{png_profile_slope}' download>⬇️ Download</a>\n")
                f.write("</div>\n")
                f.write("<div class='plotbox'>\n")
                f.write(f"<img class='plot' src='{png_profile_slope}' alt='perfil_pendiente_{fid}'>\n")
                f.write("</div>\n")
                f.write("</div>\n")

                f.write("</div>\n</section>\n")

            f.write("</div>\n")  # list

            repo_url = "https://github.com/Javisionario/L-RAT/tree/main"

            f.write("<div class='foot'>\n")
            f.write("Generated by <strong>L-RAT</strong> in QGIS. ")
            f.write(f"<a href='{repo_url}' target='_blank' rel='noopener noreferrer'>"
                    "About</a>\n")
            f.write("</div>\n")
            
            f.write("</div>\n</body>\n</html>\n")

        return {
            self.OUT_REPORT: html_path,
            self.OUT_FOLDER: out_folder,
        }
