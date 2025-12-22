# L-RAT (Linear Referencing & Analysis Tools) — QGIS Processing Plugin

L-RAT es un **plugin de Processing para QGIS** orientado a flujos de trabajo de **ingeniería viaria e infraestructuras lineales**. Incluye herramientas para:

- **Referenciación lineal (PK/M)** sobre líneas calibradas con geometría **LineStringM / MultiLineStringM** (localización de puntos y extracción de tramos).
- Utilidades complementarias habituales en análisis de viario (curvas/centros, pendientes y perfil longitudinal).

[Repositorio](https://github.com/Javisionario/L-RAT)

---

## Índice

- [1. Requisitos y conceptos](#1-requisitos-y-conceptos)
  - [1.1. Qué es una geometría M](#11-qué-es-una-geometría-m)
  - [1.2. Unidades del campo M](#12-unidades-del-campo-m)
  - [1.3. Campo ROUTE_ID](#13-campo-route_id)
  - [1.4. Formato del PK](#14-formato-del-pk)
  - [1.5. Ajuste de PK sobre la geometría: huecos entre tramos calibrados](#15-ajuste-de-pk-sobre-la-geometría-huecos-entre-tramos-calibrados)
  - [1.6. Tabla de incidencias](#16-tabla-de-incidencias)
- [2. Instalación](#2-instalación)
- [3. Estructura del plugin](#3-estructura-del-plugin)
- [4. Locate points (requires M Geometry)](#4-locate-points-requires-m-geometry)
  - [4.1. Locate points](#41-locate-points)
  - [4.2. Locate points from table](#42-locate-points-from-table)
- [5. Locate segments (requires M Geometry)](#5-locate-segments-requires-m-geometry)
  - [5.1. Locate segments](#51-locate-segments)
  - [5.2. Locate segments from PK table](#52-locate-segments-from-pk-table)
  - [5.3. Locate segments from segment table](#53-locate-segments-from-segment-table)
- [6. Miscellaneous](#6-miscellaneous)
  - [6.1. Extraer curvas y centroides](#61-extraer-curvas-y-centroides)
  - [6.2. Slope and Longitudinal Profile](#62-slope-and-longitudinal-profile)
- [7. Licencia](#7-licencia)
- [8. Autor](#8-autor)

---

## 1. Requisitos y conceptos

### 1.1. Qué es una geometría M

Las capas **LineStringM / MultiLineStringM** almacenan, además de X/Y (y opcionalmente Z), un valor **M** por vértice. En carreteras es habitual usar **M como kilometraje/PK**, de forma que se puede interpolar una posición a lo largo de la línea a partir de un PK.

L-RAT usa ese valor **M** para:
- **Localizar puntos** (eventos puntuales) en un PK.
- **Extraer segmentos** entre PK inicial y PK final.

### 1.2. Unidades del campo M

En los algoritmos “Locate …” existe un parámetro de **unidades de M** (por ejemplo, M en **kilómetros** o en **metros**). Una configuración incorrecta de este parámetro producirá resultados erróneos en la localización o interpolación.

### 1.3. Campo ROUTE_ID

Los algoritmos de referenciación lineal trabajan por **vía, carretera o ruta**. Típicamente:
- Una capa lineal calibrada (viario) con un campo tipo **ROUTE_ID** (id de carretera/tramo/eje). 
- Una tabla/capa de eventos (puntos o tramos) que referencian esa misma ruta mediante un campo equivalente.

> NOTA: Si en la capa una via está dividida en varios features (muy común), L-RAT agrupa por `ROUTE_ID` y trabaja con el conjunto de geometrías de esa ruta.

### 1.4. Formato del PK

L-RAT intenta ser flexible con la entrada de PK aceptando formatos típicos como `km+mmm` o numero decimal en km por ejemplo (`12+345`, `12.345` o `3.05`).  

### 1.5. Ajuste de PK sobre la geometría: huecos entre tramos calibrados

En capas lineales calibradas por tramos pueden existir **discontinuidades o huecos** en la geometría. 
Cuando un PK solicitado **no puede localizarse directamente sobre la geometría**, L-RAT aplica distintos mecanismos de ajuste y deja constancia de ello en la salida.

Los campos relacionados con el ajuste son:

- `PK_REQ`: PK solicitado por el usuario.
- `PK`: PK real o el PK empleado para localizar el punto o segmento (ya ajustado si aplica).
- `ADJUSTED`:
  - `0` → no se ha aplicado ajuste
  - `1` → se ha aplicado algún ajuste
- `ADJUST_REASON`: motivo(s) del ajuste, separados por `;`.

Los motivos posibles son:

- **`OUT_OF_RANGE`**  
  El PK solicitado está fuera del rango global disponible de la vía (`M_min – M_max`) y se ha recortado al extremo más cercano.

- **`GAP_SNAP`**  
  El PK solicitado está dentro del rango global, pero cae en un **hueco entre tramos calibrados**.  
  Si está activado el ajuste en huecos, el PK se ajusta al PK disponible más cercano.

### 1.6. Tabla de incidencias

Varios algoritmos permiten generar una **tabla de incidencias** (sin geometría). Esta tabla permite revisar de forma sistemática qué puntos se han ajustado, omitido o no han podido procesarse,
especialmente útil en ejecuciones por lotes o en modelos de QGIS.
- Un mismo evento puede tener **varios avisos simultáneamente**.
- Los errores críticos son **excluyentes**: si se produce uno, no hay salida geométrica.

Si esta opción está activada, se genera una tabla cuando se producen:
- ajustes de PK,
- avisos (*warnings*),
- errores críticos.

#### Ajustes (ADJUSTED / ADJUST_REASON)

Un **ajuste** significa que el algoritmo **sí genera salida**, pero ha tenido que modificar el PK para poder localizarlo.

Campos:
- `PK_REQ`: PK solicitado
- `PK`: PK finalmente utilizado
- `ADJUSTED`: 1 si hubo ajuste
- `ADJUST_REASON`: uno o varios motivos, separados por `;`

Motivos:
- **`OUT_OF_RANGE`**: PK fuera del rango global disponible (`M_min – M_max`) y recortado al extremo más cercano.
- **`GAP_SNAP`**: PK dentro del rango global, pero en un hueco entre tramos calibrados; se ajusta al PK disponible más cercano (solo si el “ajuste en huecos” está activado)

#### Avisos (*Warnings*)

Los avisos indican que el algoritmo **ha podido generar salida**, pero que se ha producido alguna situación que conviene revisar:

- **`SEGMENT_SPLIT`**: El segmento resultante se ha generado en **varias piezas** (`N_PIECES > 1`), normalmente por discontinuidades en la geometría o a que la vía está dividida en varios tramos independientes.
- **`ODD_PK_IGNORED`** (solo en “Locate segments from PK table”): Se ha encontrado un PK sin pareja y se ha ignorado para la generación de segmentos.

---

#### ❌ Errores críticos (*Criticals*)

Los errores críticos indican que el algoritmo **no ha podido generar salida** para ese punto concreto. El algoritmo registra:

- **`NO_ROUTE`**: El `ROUTE_ID` indicado no existe en la capa lineal de referencia.
- **`PK_INVALID`**: El PK no puede interpretarse correctamente (formato no válido o valor no numérico).
- **`NO_M_RANGE`**: No existe un rango M válido para la ruta indicada (geometría sin valores M utilizables).
- **`NO_MATCH`**: El PK no puede localizarse sobre la geometría y:
  - el ajuste en huecos está desactivado, o
  - no existe ningún PK válido cercano al que ajustarse.

Cuando se produce un error crítico:
- **no se genera** el punto o segmento correspondiente,
- el evento queda registrado únicamente en la tabla de incidencias.

---

## 2. Instalación

- **Desde el repositorio oficial de plugins (recomendado)**: cuando esté publicado, instálalo desde **Plugins → Administrar e instalar plugins**.
- **Desde ZIP/GitHub** (desarrollo):
  1. Descarga el ZIP del repositorio.
  2. Descomprime en tu carpeta de plugins de QGIS (perfil de usuario).
  3. Reinicia QGIS y actívalo en el gestor de plugins.

Requisito mínimo: **QGIS 3.10**

---

## 3. Estructura del plugin

Los algoritmos aparecen en la **Caja de herramientas de Processing** bajo tres subgrupos:

- **Locate points (requires M geometry)**
- **Locate segments (requires M geometry)**
- **Miscellaneous**

---

## 4. Locate points (requires M geometry)

### Parámetros comunes (puntos)

Estos algoritmos comparten ideas y parámetros muy similares:

- **Capa lineal calibrada (LineStringM/MultiLineStringM)**: red/eje con valores M.
- **Campo ROUTE_ID en la capa lineal**.
- **Unidades de M** (km o m).

- **Tolerancia (km) para encaje por M (snap/rounding)**  
  Útil para resolver pequeños desajustes **dentro de un tramo calibrado** (redondeos o calibración imperfecta).  
  No “rellena” huecos: solo ayuda si el PK cae *muy cerca* de un valor existente/interpolable.

- **Ajustar al PK disponible más cercano en caso de geometría incompleta (huecos)**  
  Controla qué ocurre cuando el PK cae en un hueco entre tramos:
  - **Activado**: aplica `GAP_SNAP`, el punto se genera y queda marcado en atributos (`ADJUSTED=1`, `ADJUST_REASON=GAP_SNAP`).
  - **Desactivado**: el evento se marca como crítico `NO_MATCH` y no se crea el punto.

- **Generar tabla de incidencias**  
  Si está activada, genera una tabla cuando existan ajustes, avisos o errores críticos.

---

### 4.1. Locate points

**Qué hace**  
Localiza puntos sobre una ruta calibrada a partir de pares `ROUTE_ID + PK` introducidos manualmente.

**Entradas**
- Capa lineal M.
- Campo ROUTE_ID.
- Uno o dos puntos a localizar segun su `ROUTE_ID`, `PK`).  
  (Según configuración, puede incluir además un identificador `ID` para conservarlo en salida.)

**Salidas**
1. **Capa de puntos** con campos típicos:
   - `ROUTE_ID`
   - `PK_ID` (si se usa)
   - `PK_REQ` (PK solicitado)
   - `PK` (PK utilizado)
   - `ADJUSTED` (0/1)
   - `ADJUST_REASON` (p.ej. `OUT_OF_RANGE`, `GAP_SNAP`)
   - `STATUS`

2. **Tabla de incidencias** (opcional) con:
   - `ROUTE_ID`, `PK_ID` (si aplica), `PK_REQ`
   - `ADJUSTED`, `ADJUST_REASON`
   - `WARNINGS`, `CRITICALS`

> NOTA: Si hay ajuste, se marca `ADJUSTED=1` y queda reflejado en atributos.

---

### 4.2. Locate points from table

**Qué hace**  
Localiza puntos usando una **tabla/capa de eventos** donde cada fila contiene un punto:
- `ROUTE_ID`
- `PK`
- y opcionalmente un identificador de evento (`PK_ID`).

**Entradas**
- Capa lineal M + campo ROUTE_ID.
- Tabla/capa de eventos:
  - campo ROUTE_ID (en eventos)
  - campo PK (en eventos)
  - Opcional:
    - añadir campos de la tabla a la salida
    - definir `PK_ID`

**Salida**
- **Capa de puntos** con:
  - `ROUTE_ID`
  - `PK_ID` (si se proporciona)
  - `PK_REQ`, `PK`, `ADJUSTED`, `ADJUST_REASON`, `STATUS`
- **Tabla de incidencias** (opcional) con:
   - `ROUTE_ID`, `PK_ID` (si aplica), `PK_REQ`
   - `ADJUSTED`, `ADJUST_REASON`
   - `WARNINGS`, `CRITICALS`

> NOTA: Si hay ajuste, se marca `ADJUSTED=1` y queda reflejado en atributos.


---

## 5. Locate segments (requires M geometry)

Algoritmos para extraer **segmentos** definidos por PK inicio y PK fin sobre una capa lineal calibrada.

### Parámetros comunes (segmentos)

- **Capa lineal calibrada M** y campo ROUTE_ID.
- **Unidades de M**.
- **Tolerancia (km)** para encaje de extremos por M.
- **Ajustar a PK disponible más cercano en huecos**.
- **Generar tabla de incidencias**.
- Opción de **generar puntos** en extremos del segmento (cuando el algoritmo lo permita).

Campos típicos en segmentos:
- `ROUTE_ID`
- `SEG_ID` (si aplica)
- `PK_INI`, `PK_FIN`
- `DIST_PK_KM` (distancia “según PK”)
- `DIST_GEOM_KM` (longitud geométrica real)
- `ADJUSTED`, `ADJUST_REASON`
- `N_PIECES` (número de piezas/fragmentos del segmento)
- `STATUS`

> Nota: `N_PIECES > 1` suele indicar que el segmento atraviesa discontinuidades o que la ruta está fragmentada; el tramo extraído puede ser multipart.

---

### 5.1. Locate segments

**Qué hace**  
Extrae segmentos a partir de pares `ROUTE_ID + PK_INI + PK_FIN` introducidos manualmente.

**Entradas**
- Capa lineal M y ROUTE_ID.
- Segmentos (ROUTE_ID, PK_INI, PK_FIN).
- Opcional: identificador `SEG_ID`.

**Salidas**
1. **Capa de líneas** con los campos descritos arriba.
2. **Capa de puntos** (opcional) con puntos en extremos:
   - `ROUTE_ID`, `SEG_ID` (si aplica)
   - `PK_REQ` (por extremo)
   - `PK` (PK utilizado)
   - `ADJUSTED`, `ADJUST_REASON`
3. **Tabla de incidencias** (opcional) con:
   - `ROUTE_ID`, `SEG_ID` (si aplica),
   - `PK_INI_REQ`, `PK_FIN_REQ`,
   - `ADJUSTED`, `ADJUST_REASON`
   - `WARNINGS`, `CRITICALS`

---

### 5.2. Locate segments from PK table

**Qué hace**  
Genera segmentos desde una tabla donde **cada fila aporta un PK y un identificador de emparejado**. Identifica los segmentos mediante la relación de pares de PKs con dicho identificador que ha de ser unico para cada segmento para un correcto funcionamiento del algoritmo.

Para cada combinación `(ROUTE_ID, PAIR_ID)`:
- los PK se ordenan numéricamente,
- se emparejan secuencialmente (0–1, 2–3, …),
- si queda un PK suelto, se ignora y se registra un aviso.

**Entradas**
- Capa lineal M + ROUTE_ID.
- Tabla de PK:
  - `ROUTE_ID`
  - `PAIR_ID`
  - `PK`
- Opcional:
  - añadir campos de la tabla a la salida
  - generar puntos extremos
  - tabla de incidencias

**Salidas**
- Capa de segmentos.
- Puntos extremos (opcional).
- Tabla de incidencias (opcional) con:
   - `ROUTE_ID`, `SEG_ID` (si aplica),
   - `PK_INI_REQ`, `PK_FIN_REQ`,
   - `ADJUSTED`, `ADJUST_REASON`
   - `WARNINGS`, `CRITICALS`

---

### 5.3. Locate segments from segment table

**Qué hace**  
Extrae segmentos desde una **tabla de segmentos** (cada fila define un tramo con PK inicio/fin).

**Entradas**
- Capa lineal M y campo ROUTE_ID.
- Tabla de segmentos con campos:
  - route id (en tabla)
  - PK inicio
  - PK fin
  - opcional: `EVENT_ID` permite transportar un campo de la tabla de origen.

**Parámetros importantes**
- Copiar campos de la tabla a la salida.
- Generar puntos extremos.
- Generar tabla de incidencias.

**Salidas**
- Segmentos (líneas), con ajuste y conteo de piezas (`N_PIECES`).
- Puntos extremos (opcional).
- Tabla de incidencias (opcional).

---

## 6. Miscellaneous

### 6.1. Extraer curvas y centroides

**Qué hace**  
Extrae segmentos de curva de una capa lineal, calcula radios de curvatura y, opcionalmente, agrupa centros cercanos (clusters). Genera hasta dos capas: **Curvas** y **Centroides**.

#### Salidas

**1) Capa Curvas (líneas)**  
Cada entidad representa un segmento de curva con atributos:
- `ID_Curva`: identificador único del segmento de curva
- `ID_Centroide`: id del cluster asociado (o -1 si no aplica)
- `Radio`: radio de curvatura (m)
- `Longitud`: longitud del segmento (m)

**2) Capa Centroides (opcional, puntos)**  
Cada entidad representa un cluster de centros de curva:
- `ID_Centroide`: id del cluster
- `Radio_medio`: radio medio de las curvas del cluster (m)
- `Conteo`: número de curvas en el cluster

#### Parámetros

- **Capa de líneas de entrada**: capa vectorial de líneas a analizar.
- **Intervalo de densificación** (m): distancia entre vértices añadidos (por defecto 15.0).
- **Radio mínimo** (m): descarta curvas más “cerradas” que este umbral (por defecto 2.0).
- **Radio máximo** (m): descarta curvas demasiado “planas” (por defecto 50.0).
- **Distancia mínima entre vértices** (m): ignora tríos de puntos demasiado cercanos (por defecto 0.5).
- **Generar capa de centros de curva** (bool): si se activa, produce la capa de centroides/clusters (por defecto False).
- **Distancia de agrupación de centros** (m): distancia máxima para agrupar centros en el mismo cluster (por defecto 10.0).

#### Metodología

1. **Densificación**: se densifica la línea para tener vértices a intervalos regulares.
2. Para cada tripleta consecutiva `(p1, p2, p3)`:
   - Se calculan longitudes `a=dist(p1,p2)`, `b=dist(p2,p3)`, `c=dist(p3,p1)`.
   - Se calcula el semiperímetro `s=(a+b+c)/2`.
   - Se calcula el área por Herón: `area = sqrt(s(s−a)(s−b)(s−c))`.
   - Se calcula el radio del circuncírculo: `R = (a·b·c)/(4·area)`.
   - Si los puntos son casi colineales (`area≈0`), no se calcula radio (se omite).
3. (Opcional) Se calculan centros de curvatura y se **agrupan por distancia**.

#### Limitaciones y recomendaciones

- Usa un **CRS proyectado en metros** (UTM o similar). En geográficas (lat/long) las distancias serán incorrectas.
- Si la geometría tiene ruido, puede producir curvas falsas.
- El intervalo de densificación es clave:
  - demasiado grande → perderás curvas
  - demasiado pequeño → aumenta el coste y puede generar exceso de detecciones
- Si la capa de centroides sale vacía:
  - activa “Generar capa de centros de curva”
  - asegúrate de que el rango de radios y filtros permite detectar curvas válidas

---

### 6.2. Slope and Longitudinal Profile

**Qué hace**  
A partir de una línea (eje) y un DEM, genera:
- un **perfil longitudinal** (distancia acumulada vs cota)
- y una estimación de **pendientes** por tramos (en %)


#### Entradas

- **Capa de líneas de entrada**  
  Línea o conjunto de líneas que definen el eje a analizar.

- **Modelo Digital del Terreno (DEM)**  
  Raster con valores de elevación (raster local o WCS).

- **Campo identificador (opcional)**  
  Permite conservar un identificador del eje en las salidas.

- **Paso de muestreo (m)**  
  Distancia entre puntos consecutivos donde se muestrea el DEM a lo largo de la línea.

- **Invertir sentido del perfil** (opcional)  
  Cambia el origen del perfil (inicio ↔ fin de la línea).

---

#### Salidas

1. **Tabla de perfil longitudinal (sin geometría)**  
   Una fila por muestra, con:
   - distancia acumulada desde el origen,
   - cota,
   - cota suavizada
   - pendiente (%),
   - tipo de valor de pendiente (`REAL`, `INTERP`, `EXTRAP`, `NODATA`).
   - valor del PK (opcional)

   Pensada para:
   - análisis numérico,
   - gráficos de perfil longitudinal,
   - exportación a hojas de cálculo.

2. **Capa de líneas segmentadas (micro-tramos)**  
   La línea original se divide en segmentos entre muestras consecutivas, con atributos de cota y pendiente, pensados para:
   - simbología por pendiente,
   - identificación visual de tramos críticos.

> Nota: esta capa **no conserva valores M**, ya que su objetivo es la representación y análisis del perfil.

---
#### Paso de muestreo

El **paso de muestreo** controla la resolución del perfil y del cálculo de pendientes:

- Valores **pequeños**:
  - mayor detalle,
  - mayor sensibilidad al ruido del DEM,
  - mayor coste computacional.
- Valores **grandes**:
  - perfiles más suaves,
  - menor detalle,
  - pueden ocultar cambios locales.

**Recomendación práctica**: Como regla general, aplicar un paso como mínimo de 4x el tamaño del pixel del DEM.
Si el paso es 0, se calcula automáticamente a partir de la resolución real del DEM, convertida a metros.

#### Modos de muestreo del DEM (resampling)

Controlan cómo se calcula la cota en cada punto muestreado sobre la línea:

- **Nearest neighbour**: toma el valor del píxel más cercano. Método rápido que conserva los valores originales si bien puede ofrecer un resultado escalonado.
- **Bilinear**: interpola usando 4 píxeles vecinos, ofreciendo un resultado suave y estable.
- **Cubic**: interpola usando un vecindario mayor (más suave, más costoso; puede hacer fallback si no se puede calcular).

#### Suavizado (métodos)

Suaviza la serie de elevaciones para reducir ruido y estabilizar el cálculo de pendientes:

- **Sin suavizado**
- **Media móvil** (moving average)
  Sustituye cada valor por el promedio de la ventana. Suaviza bastante el ruido, pero puede verse afectada por valores extremos (picos/artefactos).  
  En igualdad de ventana, suele suavizar **más que Savitzky–Golay** y **de forma más “aplanadora”**.
- **Mediana móvil** (moving median)
  Sustituye cada valor por la mediana de la ventana. Es robusta frente a valores extremos, por lo que suele ser una buena opción cuando el DEM tiene artefactos locales (árboles, puentes...)
- **Savitzky–Golay**
  Suaviza preservando la forma mejor que la media movil, aunque puede introducir ruido ante una ventana pequeña y un orden elevado.

**Ventana de suavizado**
- Número de muestras usadas en el filtro.
- Ventanas pequeñas suavizan poco; ventanas grandes suavizan más pero pueden “aplanar” cambios reales. Tipicamente, 7-15.

**Orden polinómico (Savitzky–Golay)**
- Grado del polinomio ajustado dentro de cada ventana.
- Valores altos preservan mejor formas complejas, pero pueden amplificar ruido si la ventana es pequeña.

#### Pendiente

La pendiente se calcula entre muestras consecutivas como:

- `slope% = 100 * dz / dx`

Donde:

- `dz` es la diferencia de cota,
- `dx` es la distancia horizontal entre muestras.

> NOTA: Cuando faltan valores de cota consecutivos, la pendiente se interpola o extrapola para mantener una capa de segmentos continua.

#### Recomendaciones

- DEM y línea deben estar en un CRS proyectado para obtener pendientes en % basadas en metros.
- Ante un DEM ruidoso, se recomienda **mediana móvil** o **Savitzky–Golay**.

---

## 7. Licencia

Este proyecto se distribuye bajo la **GNU General Public License v3.0 (GPL-3.0)**.  
Puedes usarlo, modificarlo y compartirlo libremente bajo los términos de esta licencia.

---

## 8. Autor

- **LinkedIn**: [Javi H. Piris](https://www.linkedin.com/in/javierhpiris)  
- **GitHub**: [@Javisionario](https://github.com/Javisionario)
