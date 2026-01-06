# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from qgis.PyQt.QtCore import QSettings, QLocale


def _qgis_lang_2() -> str:
    """
    Intenta usar el idioma configurado en QGIS.
    Devuelve 'es', 'en', etc.
    """
    # Suele venir como 'es_ES' o 'en_GB'
    v = QSettings().value("locale/userLocale", "", type=str)
    if v:
        return v.split("_")[0].lower()
    return QLocale().name().split("_")[0].lower()


def short_help(alg_key: str, default_lang: str = "es") -> str:
    """
    Carga help/<lang>/<alg_key>.html con fallback:
      1) idioma de QGIS
      2) default_lang
      3) en
    """
    lang = _qgis_lang_2()

    # Este fichero está en .../help/short_help.py
    plugin_root = Path(__file__).resolve().parents[1]  # sube desde help/ a raíz
    help_dir = plugin_root / "help"

    candidates = [
        help_dir / lang / f"{alg_key}.html",
        help_dir / default_lang / f"{alg_key}.html",
        help_dir / "en" / f"{alg_key}.html",
    ]

    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")

    # Fallback final si falta el fichero
    return f"<b>Help missing</b><br>No help file found for <code>{alg_key}</code>."
