# -*- coding: utf-8 -*-
import os

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, QTranslator

from .linear_rat_provider import LinearRATProvider


class LinearRATPlugin:
    """registra/desregistra el Processing provider + i18n."""

    def __init__(self, iface):
        self.iface = iface
        self.provider = None

        # Traductor del plugin
        self._translator = None
        self._install_translator()

    def _install_translator(self):
        """
        Carga i18n/linear_rat_<locale>.qm (p.ej. linear_rat_en_GB.qm)
        y hace fallback a i18n/linear_rat_<lang>.qm (p.ej. linear_rat_en.qm).
        """
        plugin_dir = os.path.dirname(__file__)
        i18n_dir = os.path.join(plugin_dir, "i18n")

        locale = QgsApplication.locale() or ""  # p.ej. "es_ES", "en_US"
        lang = locale[:2] if len(locale) >= 2 else ""

        candidates = []
        if locale:
            candidates.append(os.path.join(i18n_dir, f"linear_rat_{locale}.qm"))
        if lang:
            candidates.append(os.path.join(i18n_dir, f"linear_rat_{lang}.qm"))

        for qm_path in candidates:
            if os.path.exists(qm_path):
                tr = QTranslator()
                if tr.load(qm_path):
                    QCoreApplication.installTranslator(tr)
                    self._translator = tr
                break

    def initGui(self):
        """Se llama al activar el plugin."""
        if self.provider is None:
            self.provider = LinearRATProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        """Se llama al desactivar el plugin."""
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

        if self._translator is not None:
            QCoreApplication.removeTranslator(self._translator)
            self._translator = None
