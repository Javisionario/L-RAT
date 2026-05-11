# AGENTS.md

Instrucciones de trabajo para agentes y colaboradores que modifiquen este repositorio.

## Contexto Del Proyecto

- Este repositorio contiene un plugin de QGIS llamado L-RAT.
- La carpeta real del plugin es `linear_rat`.
- L-RAT añade herramientas a la caja de Processing de QGIS para trabajar con capas lineales calibradas, especialmente geometrías `LineStringM` y `MultiLineStringM`.
- Sus funciones principales son localizar puntos y segmentos sobre líneas calibradas, trabajar con tablas de eventos, calibrar líneas, editar valores M, estimar radios de curva y obtener perfiles longitudinales basados en la calibración.
- El plugin está publicado en el repositorio oficial de plugins de QGIS, por lo que la compatibilidad hacia atrás importa.

## Reglas Generales

- No cambiar el provider id ni los `name()` públicos de los algoritmos salvo instrucción explícita.
- No cambiar `metadata.txt`, `version`, `qgisMinimumVersion` ni `qgisMaximumVersion` sin instrucción explícita.
- Mantener como objetivo principal la compatibilidad con QGIS 3.x.
- Tener en cuenta compatibilidad futura con QGIS 4.x, pero no declararla en `metadata.txt` hasta que se haya probado expresamente.
- Evitar APIs obsoletas o dependencias Qt5-only cuando haya alternativas razonables.
- Usar imports de Qt a través de `qgis.PyQt` cuando sea necesario, no `PyQt5`/`PyQt6` directamente salvo justificación.
- Mantener soporte para `LineStringM` y `MultiLineStringM`.
- Ser especialmente cuidadoso con geometrías multipart, rutas partidas, valores M no monótonos, gaps y geometrías con varias partes.

## Compatibilidad De Outputs

- No eliminar, renombrar ni cambiar el significado de campos de salida existentes en correcciones menores sin justificar el impacto de compatibilidad.
- Si una mejora de UX o simplificación requiere cambiar campos de salida, proponer antes un plan explícito: mantener compatibilidad, añadir campos nuevos, deprecar campos antiguos o crear un algoritmo v2.
- No sobrescribir campos existentes de capas de entrada. Si hay colisión de nombres, resolverla de forma segura y documentada.
- Mantener los nombres actuales de campos de salida cuando no haya colisión.

## Forma De Trabajar

- Evitar refactors grandes si una corrección pequeña resuelve el problema.
- Separar cambios funcionales, UX, documentación, traducciones y versión en commits distintos.
- Antes de tocar una herramienta, identificar el comportamiento actual, el comportamiento esperado y el riesgo de compatibilidad.
- En cambios sobre algoritmos Processing, explicar inputs, outputs, geometrías aceptadas y riesgos de compatibilidad.
- Usar `self.tr()` para textos visibles nuevos o modificados cuando sea razonable.
- No mezclar cambios de traducción/terminología con correcciones funcionales salvo que sea imprescindible.
- No introducir dependencias obligatorias nuevas sin justificarlo.
- Si una dependencia externa como `numpy` o `matplotlib` es opcional, evitar que su ausencia impida cargar todo el provider.
- Después de editar, entregar un resumen de archivos modificados, motivo del cambio y pruebas manuales recomendadas en QGIS.

## Prioridades Técnicas

- Corregir primero errores funcionales y problemas de carga del plugin.
- Revisar después la robustez con `LineStringM` y `MultiLineStringM`.
- Revisar herramientas frágiles como `calibrate_points` y `calibrate_linestringm_from_points` con cambios pequeños y testeables.
- Mejorar después UX, terminología e inputs de forma incremental.
- Dejar cambios de `metadata.txt`, versión, changelog y publicación para el final.
