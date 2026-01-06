# -*- coding: utf-8 -*-
from .plugin import LinearRATPlugin


def classFactory(iface):
    return LinearRATPlugin(iface)
