# -*- coding: utf-8 -*-
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

# Algoritmos 
from .locate_segments.locate_segments import LocateManualSegments
from .locate_segments.locate_segments_from_pk_table import LocateSegmentsFromPKTable
from .locate_segments.locate_segments_from_segment_table import LocateSegmentsFromSegmentTable

from .locate_points.locate_points import LocatePoints
from .locate_points.locate_points_from_table import LocatePointsFromTable

from .profile_slope.slope_and_profile import SlopeAndLongitudinalProfileAlgorithm
from .profile_slope.profile_slope_plots import ProfileSlopePlotsAlgorithm

from .calibration.calibrate_points import CalibratePoints
from .calibration.CalibrateLinesFromDistance import CalibrateLinesFromDistance
from .calibration.ModifyMGeometry import ModifyMGeometry
from .calibration.CalibrateLineStringMFromPoints import CalibrateLineStringMFromPoints

from .miscellaneous.extract_curves_centroids import ExtractCurvesAndCentroids

class LinearRATProvider(QgsProcessingProvider):

    def id(self):
        return "l-rat"

    def name(self):
        return "L-RAT"

    def longName(self):
        return "Linear Referencing & Analysis Tools"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(LocateManualSegments())
        self.addAlgorithm(LocateSegmentsFromPKTable())
        self.addAlgorithm(LocateSegmentsFromSegmentTable())
        self.addAlgorithm(ExtractCurvesAndCentroids())
        self.addAlgorithm(LocatePoints())
        self.addAlgorithm(LocatePointsFromTable())
        self.addAlgorithm(SlopeAndLongitudinalProfileAlgorithm())
        self.addAlgorithm(ProfileSlopePlotsAlgorithm())
        self.addAlgorithm(CalibratePoints())
        self.addAlgorithm(CalibrateLinesFromDistance())
        self.addAlgorithm(ModifyMGeometry())
        self.addAlgorithm(CalibrateLineStringMFromPoints())
        