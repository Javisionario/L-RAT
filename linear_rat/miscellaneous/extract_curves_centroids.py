# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import processing

from qgis.PyQt.QtCore import QVariant, QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterDistance,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsFeatureSink,
    QgsWkbTypes,
    QgsSpatialIndex,
    QgsRectangle,
)


class ExtractCurvesAndCentroids(QgsProcessingAlgorithm):
    """
    Extracts curve segments from densified lines, computes curvature radius,
    clusters nearby centers and generates two layers:
      • Curvas: Curve_ID, Center_ID, Radius, Length
      • Centroides: Center_ID, Mean_Radius, Count
    """

    INPUT_LAYER = "INPUT_LAYER"
    INTERVAL = "INTERVAL"
    MIN_RADIUS = "MIN_RADIUS"
    MAX_RADIUS = "MAX_RADIUS"
    MIN_DIST = "MIN_DIST"
    ADD_CENTERS = "ADD_CENTERS"
    CLUSTER_DISTANCE = "CLUSTER_DISTANCE"
    OUTPUT_CURVES = "OUTPUT_CURVES"
    OUTPUT_CENTERS = "OUTPUT_CENTERS"

    def tr(self, s: str) -> str:
        return QCoreApplication.translate(self.__class__.__name__, s)
    
    def initAlgorithm(self, config=None):
        # Parámetros de entrada
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_LAYER,
                self.tr("Input line layer"),
                [QgsProcessing.TypeVectorLine],
            )
        )

        # Distancias como "Distance" ligadas a la capa (mejor UX / unidades coherentes)
        self.addParameter(
            QgsProcessingParameterDistance(
                self.INTERVAL,
                self.tr("Densification interval"),
                defaultValue=15.0,
                parentParameterName=self.INPUT_LAYER,
                minValue=0.000001,
            )
        )
        self.addParameter(
            QgsProcessingParameterDistance(
                self.MIN_RADIUS,
                self.tr("Minimum radius"),
                defaultValue=2.0,
                parentParameterName=self.INPUT_LAYER,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterDistance(
                self.MAX_RADIUS,
                self.tr("Maximum radius"),
                defaultValue=50.0,
                parentParameterName=self.INPUT_LAYER,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterDistance(
                self.MIN_DIST,
                self.tr("Minimum distance between vertices"),
                defaultValue=0.5,
                parentParameterName=self.INPUT_LAYER,
                minValue=0.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ADD_CENTERS,
                self.tr("Create curve centers layer"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterDistance(
                self.CLUSTER_DISTANCE,
                self.tr("Center clustering distance"),
                defaultValue=10.0,
                parentParameterName=self.INPUT_LAYER,
                minValue=0.0,
            )
        )

        # Salidas
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_CURVES,
                self.tr("Curve segments layer"),
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_CENTERS,
                self.tr("Centroids layer (clustered)"),
                optional=True,
            )
        )

    def name(self):
        return "extract_curves_centroids"

    def displayName(self):
        return "Curve Detection and Curvature Centers"

    def group(self):
        return "Miscellaneous"

    def groupId(self):
        return "miscellaneous"

    def createInstance(self):
        return ExtractCurvesAndCentroids()

    def checkParameterValues(self, parameters, context):
        errores = []
        intervalo = parameters.get(self.INTERVAL)
        md = parameters.get(self.MIN_DIST)
        rmin = parameters.get(self.MIN_RADIUS)
        rmax = parameters.get(self.MAX_RADIUS)
        cd = parameters.get(self.CLUSTER_DISTANCE)

        try:
            if intervalo is None or float(intervalo) <= 0:
                errores.append(self.tr("• Densification interval must be > 0"))
            if md is None or float(md) < 0:
                errores.append(self.tr("• Minimum distance between vertices cannot be negative"))
            if rmin is None or rmax is None or float(rmin) >= float(rmax):
                errores.append(self.tr("• Minimum radius must be smaller than maximum radius"))
            if cd is None or float(cd) < 0:
                errores.append(self.tr("• Center clustering distance must be ≥ 0"))
        except Exception:
            errores.append(self.tr("• Invalid numeric parameters"))

        if errores:
            return False, "\n".join(errores)
        return True, ""

    @staticmethod
    def circle_radius(p1: QgsPointXY, p2: QgsPointXY, p3: QgsPointXY):
        """
        Calcula el radio de la circunferencia circunscrita a los tres puntos usando Herón:
          s = (a + b + c) / 2
          área = sqrt[s(s−a)(s−b)(s−c)]
          radio = (a·b·c) / (4·área)
        """
        a = p1.distance(p2)
        b = p2.distance(p3)
        c = p3.distance(p1)
        s = (a + b + c) / 2.0
        try:
            area2 = s * (s - a) * (s - b) * (s - c)
            if area2 <= 1e-18:
                return None
            area = math.sqrt(area2)
            return (a * b * c) / (4.0 * area)
        except Exception:
            return None

    @staticmethod
    def circle_center(p1: QgsPointXY, p2: QgsPointXY, p3: QgsPointXY):
        """
        Centro (Ux, Uy) de la circunferencia circunscrita.
        Si d≈0 (puntos colineales), devuelve None.
        """
        ax, ay = p1.x(), p1.y()
        bx, by = p2.x(), p2.y()
        cx, cy = p3.x(), p3.y()

        d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-9:
            return None

        ux = (
            (ax * ax + ay * ay) * (by - cy)
            + (bx * bx + by * by) * (cy - ay)
            + (cx * cx + cy * cy) * (ay - by)
        ) / d
        uy = (
            (ax * ax + ay * ay) * (cx - bx)
            + (bx * bx + by * by) * (ax - cx)
            + (cx * cx + cy * cy) * (bx - ax)
        ) / d

        return QgsPointXY(ux, uy)

    @staticmethod
    def _rebuild_cluster_index(clusters):
        """
        Reconstruye un QgsSpatialIndex a partir de clusters.
        Se usa como fallback si deleteFeature no está disponible o para máxima compatibilidad.
        """
        idx = QgsSpatialIndex()
        for cl in clusters:
            idx.addFeature(cl["feat"])
        return idx

    def processAlgorithm(self, parameters, context, feedback):
        # 1. Leer y validar parámetros de entrada
        layer = self.parameterAsVectorLayer(parameters, self.INPUT_LAYER, context)
        intervalo = self.parameterAsDouble(parameters, self.INTERVAL, context)
        rmin = self.parameterAsDouble(parameters, self.MIN_RADIUS, context)
        rmax = self.parameterAsDouble(parameters, self.MAX_RADIUS, context)
        md = self.parameterAsDouble(parameters, self.MIN_DIST, context)
        incluir_ctr = self.parameterAsBool(parameters, self.ADD_CENTERS, context)
        dist_agrup = self.parameterAsDouble(parameters, self.CLUSTER_DISTANCE, context)

        if not layer:
            raise QgsProcessingException(self.tr("Invalid input layer"))

        # 2. Densificar geometrías para tener vértices regulares
        dens = processing.run(
            "native:densifygeometriesgivenaninterval",
            {"INPUT": layer, "INTERVAL": intervalo, "OUTPUT": "memory:dens"},
            context=context,
            feedback=feedback,
        )["OUTPUT"]

        # 3. Extraer segmentos de curva válidos
        segmentos = []
        total_f = dens.featureCount() or 1

        for i, feat in enumerate(dens.getFeatures()):
            if feedback.isCanceled():
                break

            geom = feat.geometry()
            parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]

            for line in parts:
                for j in range(len(line) - 2):
                    p1 = QgsPointXY(line[j])
                    p2 = QgsPointXY(line[j + 1])
                    p3 = QgsPointXY(line[j + 2])

                    # descartar distancias muy cortas
                    if p1.distance(p2) < md or p2.distance(p3) < md:
                        continue

                    # calcular radio y filtrar por rango
                    r = self.circle_radius(p1, p2, p3)
                    if r and (rmin < r < rmax):
                        seg_geom = QgsGeometry.fromPolylineXY([p1, p2, p3])
                        length = seg_geom.length()
                        center = self.circle_center(p1, p2, p3) if incluir_ctr else None

                        segmentos.append(
                            {"geom": seg_geom, "radio": r, "long": length, "centro": center}
                        )

            feedback.setProgress(int(100 * i / total_f))

        # 4. Agrupar centros de curva cercanos (con índice espacial)
        mapa_cluster = {}
        clusters = []

        if incluir_ctr:
            index = QgsSpatialIndex()

            for idx_seg, seg in enumerate(segmentos):
                pt = seg["centro"]
                if not pt:
                    continue

                # candidatos por bbox
                rect = QgsRectangle(
                    pt.x() - dist_agrup,
                    pt.y() - dist_agrup,
                    pt.x() + dist_agrup,
                    pt.y() + dist_agrup,
                )
                candidate_ids = index.intersects(rect)

                best_cid = None
                best_dist = None

                for cid in candidate_ids:
                    cpt = clusters[cid]["centroid"]
                    d = pt.distance(cpt)
                    if d <= dist_agrup and (best_dist is None or d < best_dist):
                        best_dist = d
                        best_cid = cid

                if best_cid is None:
                    # crear cluster nuevo
                    cid = len(clusters)
                    f = QgsFeature()
                    f.setId(cid)
                    f.setGeometry(QgsGeometry.fromPointXY(pt))

                    clusters.append(
                        {
                            "centroid": pt,
                            "indices": [idx_seg],
                            "sum_r": seg["radio"],
                            "feat": f,
                        }
                    )
                    index.addFeature(f)
                    mapa_cluster[idx_seg] = cid
                    continue

                # asignar a cluster existente
                cl = clusters[best_cid]
                cl["indices"].append(idx_seg)
                cl["sum_r"] += seg["radio"]

                # recalcular el centroide medio del cluster
                xs = [segmentos[k]["centro"].x() for k in cl["indices"]]
                ys = [segmentos[k]["centro"].y() for k in cl["indices"]]
                new_centroid = QgsPointXY(sum(xs) / len(xs), sum(ys) / len(ys))

                # actualizar geometría del "feature" del cluster
                cl["centroid"] = new_centroid
                cl["feat"].setGeometry(QgsGeometry.fromPointXY(new_centroid))

                # compatibilidad: si existe deleteFeature, actualizamos fino; si no, reconstruimos índice
                if hasattr(index, "deleteFeature"):
                    try:
                        index.deleteFeature(cl["feat"])
                        index.addFeature(cl["feat"])
                    except Exception:
                        index = self._rebuild_cluster_index(clusters)
                else:
                    index = self._rebuild_cluster_index(clusters)

                mapa_cluster[idx_seg] = best_cid

        # 5. Construir la capa de curvas (campos requeridos)
        campos_curvas = QgsFields()
        campos_curvas.append(QgsField("Curve_ID", QVariant.Int))
        campos_curvas.append(QgsField("Center_ID", QVariant.Int))
        campos_curvas.append(QgsField("Radius", QVariant.Double))
        campos_curvas.append(QgsField("Length", QVariant.Double))

        sink_curvas, id_curvas = self.parameterAsSink(
            parameters,
            self.OUTPUT_CURVES,
            context,
            campos_curvas,
            QgsWkbTypes.LineString,
            dens.sourceCrs(),
        )

        for idx, seg in enumerate(segmentos):
            f = QgsFeature(campos_curvas)
            f.setGeometry(seg["geom"])
            f.setAttribute("Curve_ID", idx)
            f.setAttribute("Center_ID", mapa_cluster.get(idx, -1))
            f.setAttribute("Radius", float(seg["radio"]))
            f.setAttribute("Length", float(seg["long"]))
            sink_curvas.addFeature(f, QgsFeatureSink.FastInsert)

        # 6. Construir la capa de centroides (si procede)
        id_centros = None
        if incluir_ctr:
            campos_ctr = QgsFields()
            campos_ctr.append(QgsField("Center_ID", QVariant.Int))
            campos_ctr.append(QgsField("Mean_Radius", QVariant.Double))
            campos_ctr.append(QgsField("Count", QVariant.Int))

            sink_ctr, id_centros = self.parameterAsSink(
                parameters,
                self.OUTPUT_CENTERS,
                context,
                campos_ctr,
                QgsWkbTypes.Point,
                dens.sourceCrs(),
            )

            for cid, cl in enumerate(clusters):
                cnt = len(cl["indices"])
                avg = (cl["sum_r"] / cnt) if cnt else 0.0

                f = QgsFeature(campos_ctr)
                f.setGeometry(QgsGeometry.fromPointXY(cl["centroid"]))
                f.setAttribute("Center_ID", cid)
                f.setAttribute("Mean_Radius", float(avg))
                f.setAttribute("Count", int(cnt))
                sink_ctr.addFeature(f, QgsFeatureSink.FastInsert)

        # 7. Mensaje resumen
        feedback.pushInfo(self.tr("=== Curve statistics ==="))
        feedback.pushInfo(self.tr("Total segments: {0}").format(len(segmentos)))
        if incluir_ctr:
            feedback.pushInfo(self.tr("Total centroids: {0}").format(len(clusters)))

        result = {self.OUTPUT_CURVES: id_curvas}
        if id_centros is not None:
            result[self.OUTPUT_CENTERS] = id_centros
        return result

    def shortHelpString(self):
        from ..help.short_help import short_help
        return short_help("curve-detection")
