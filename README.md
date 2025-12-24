# L-RAT (Linear Referencing & Analysis Tools) — QGIS Processing Plugin

L-RAT es un **plugin de Processing para QGIS** orientado a flujos de trabajo de **ingeniería viaria e infraestructuras lineales**. Incluye herramientas para:

- **Calibración (PK/M)**: añadir información PK/M a geometrías existentes (p. ej., calcular el PK de puntos por proximidad a una red calibrada; o generar/ajustar M en líneas).
- **Referenciación lineal (PK/M)**: localizar **puntos** y extraer **segmentos** sobre líneas calibradas con geometría **LineStringM / MultiLineStringM**.
- **Perfiles y pendientes**: calcular perfiles longitudinales y pendientes a partir de un eje y un DEM, y generar gráficos y salidas listas para representación cartográfica.
- **Misc**: herramientas de análisis geométrico sobre capas lineales (p. ej., detección de curvas y centroides).

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

- [7. Profile & Slope](#7-profile--slope)
  - [7.1. Slope and Longitudinal Profile](#71-slope-and-longitudinal-profile)
  - [7.2. Profile Slope Plotter](#72-profile-slope-plotter)

- [8. Calibrate M geometry](#8-calibrate-m-geometry)
  - [8.1. Calibrate points](#81-calibrate-points)
  - [8.2. Calibrate lines from distance](#82-calibrate-lines-from-distance)
  - [8.3. Modify M geometry](#83-modify-m-geometry)

- [9. Licencia](#9-licencia)
- [10. Autor](#10-autor)

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

#### 🔎 Ajustes (ADJUSTED / ADJUST_REASON)

Un **ajuste** significa que el algoritmo **sí genera salida**, pero ha tenido que modificar el PK para poder localizarlo.

Campos:
- `PK_REQ`: PK solicitado
- `PK`: PK finalmente utilizado
- `ADJUSTED`: 1 si hubo ajuste
- `ADJUST_REASON`: uno o varios motivos, separados por `;`

Motivos:
- **`OUT_OF_RANGE`**: PK fuera del rango global disponible (`M_min – M_max`) y recortado al extremo más cercano.
- **`GAP_SNAP`**: PK dentro del rango global, pero en un hueco entre tramos calibrados; se ajusta al PK disponible más cercano (solo si el “ajuste en huecos” está activado)

#### ⚠️ Avisos (*Warnings*)

Los avisos indican que el algoritmo **ha podido generar salida**, pero que se ha producido alguna situación que conviene revisar:

- **`SEGMENT_SPLIT`**: El segmento resultante se ha generado en **varias piezas** (`N_PIECES > 1`), normalmente por discontinuidades en la geometría o a que la vía está dividida en varios tramos independientes.
- **`ODD_PK_IGNORED`** (solo en “Locate segments from PK table”): Se ha encontrado un PK sin pareja y se ha ignorado para la generación de segmentos.

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

Los algoritmos aparecen en la **Caja de herramientas de Processing** bajo los siguientes subgrupos:

- **Calibrate M Geometry**
- **Locate points (requires M geometry)**
- **Locate segments (requires M geometry)**
- **Miscellaneous**
- **Profile & Slope**

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

## 7. Profile & Slope

Este grupo reúne herramientas para generar perfiles longitudinales y pendientes a partir de un DEM, y para **graficar** dichos resultados de forma automática.

### 7.1. Slope and Longitudinal Profile

**Qué hace**  
A partir de una línea (eje) y un DEM, genera:
- un **perfil longitudinal** (distancia acumulada vs cota)
- y una estimación de **pendientes** por tramos (en %)


#### Entradas

- **Capa de líneas de entrada**: Línea o conjunto de líneas que definen el eje a analizar.
- **Modelo Digital del Terreno (DEM)**: Raster con valores de elevación (raster local o WCS).
- **Campo identificador (opcional)**: Permite conservar un identificador del eje en las salidas.
- **Paso de muestreo (m)**: Distancia entre puntos consecutivos donde se muestrea el DEM a lo largo de la línea.
- **Invertir sentido del perfil** (opcional): Cambia el origen del perfil (inicio ↔ fin de la línea).


#### Salidas

1. **Tabla de perfil longitudinal (sin geometría)**: Se integra de modo sencillo con el algoritmo de L-RAT Profile Slope Plotter  
   Una fila por muestra, con:
   -`ID_Segmento`
   - `Dist_Origen_metros`: distancia acumulada desde el origen,
   - `Cota_RAW_metros`: cota muestreada
   - `Cota_SUAV`: cota suavizada
   - `SLOPE`: pendiente (en %),
   - `SLOPE_TYPE`: tipo de valor de pendiente (`REAL`, `INTERP`, `EXTRAP`, `NODATA`)
   
   Opcionalmente, si se activa **Usar M** añade el valor del PK (opcional):
   - `m_field_PK_KM`
   - `m_field_PK_MMM` :contentReference[oaicite:3]{index=3}

   Pensada para:
   - análisis numérico,
   - gráficos de perfil longitudinal,
   - exportación a hojas de cálculo.

1. **Capa de líneas segmentadas (micro-tramos)**  
   La línea original se divide en segmentos entre muestras consecutivas, con atributos de cota y pendiente, pensados para:
   - simbología por pendiente,
   - identificación visual de tramos críticos.

> Nota: esta capa **no conserva valores M**, ya que su objetivo es la representación y análisis del perfil.

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

### 7.2. Profile Slope Plotter

**Qué hace**  
Genera automáticamente gráficos en PNG y un informe HTML (index) a partir de una **tabla/dataset** con:
- distancia desde el origen (m),
- cota (perfil),
- y opcionalmente pendiente (%).

Está **pensado específicamente** para graficar la tabla creada por **“Slope and Longitudinal Profile”**, aunque puede usarse con cualquier tabla compatible.

#### Entradas
- **Tabla/capa de entrada**: dataset (puede ser sin geometría).
- **Campo ID (opcional)**: genera un conjunto de gráficos por cada ID.
- **Campo X**: distancia desde origen en metros (por defecto `Dist_Origen_metros`).
- **Campo Y**: cota a graficar (por defecto `Cota_SUAV`).
- **Campo pendiente** (opcional): pendiente en % (por defecto `SLOPE`).
- **Usar PK** (opcional): si existe un campo PK (km), etiqueta el eje X como `K+MMM`.
- **Carpeta de salida**: destino de PNG + `index.html`. :contentReference[oaicite:7]{index=7}

#### Salidas
- **PNG**: por cada ID genera:
  - Perfil longitudinal
  - Perfil + pendiente (%)
- **HTML (index)**: informe con vista previa y enlaces a todos los gráficos. :contentReference[oaicite:8]{index=8}

#### Consejos
- Si tu tabla viene de “Slope and Longitudinal Profile”, usa por defecto:
  - X = `Dist_Origen_metros`
  - Y = `Cota_SUAV`
  - Pendiente = `SLOPE`
  - PK = `m_field_PK_KM` (si activaste “Usar M” en el cálculo)

---

## 8. Calibrate M geometry

Este grupo reúne herramientas para **calibrar** o **derivar** valores relacionados con el PK/M con el objetivo de preparar datos (calibrar) para usarlos después en flujos de referenciación lineal.
Se trata de algoritmos para añadir valores de calibración a geometrías existentes.

---

### 8.1. Calibrate points

**Qué hace**  
Asigna a cada punto un **PK (km+mmm)** y un valor **M interpolado**, proyectando el punto sobre una capa lineal **calibrada** con geometría M (`LineStringM / MultiLineStringM`).  
**No modifica** la geometría del punto: añade campos de calibración en atributos.

#### Entradas

- **Capa de puntos a calibrar**
- **Capa de líneas calibrada (M)** (eje/viario)
- **Unidades del campo M**: metros o kilómetros
- **Distancia máxima (búsqueda/proyección)**: umbral para aceptar el emparejado (si el eje más cercano está más lejos, se marca incidencia)

#### Opciones

- **Restringir emparejado por ROUTE_ID** *(recomendado si hay vías paralelas)*  
  Solo busca coincidencias dentro de la misma ruta/vía.  
  Requiere indicar el campo `ROUTE_ID` tanto en **puntos** como en **líneas**.

- **Añadir ROUTE_ID a la salida (desde la capa de líneas)**  
  Copia el identificador de la línea emparejada al punto.  
  Requiere indicar el campo `ROUTE_ID` en la **capa de líneas**.  
  Si el campo `ROUTE_ID` ya existe en la capa de puntos, se crea como `ROUTE_ID_MATCH`.

- **Generar tabla de incidencias (solo si existen)** *(avanzado, por defecto desactivado)*  
  Si está activada, crea una tabla sin geometría **solo si** hubo incidencias.

#### Salidas

1) **Puntos calibrados (con PK/M)**  
Copia los atributos originales y añade:

- `PK`: PK formateado (`km+mmm`)
- `M`: valor M interpolado (en las unidades almacenadas en la geometría)
- `DIST_AXIS`: distancia del punto al eje emparejado (unidades del CRS)
- `INCIDENCE`: `0/1` (1 si no se pudo calibrar)
- `INC_TYPE`: motivo de la incidencia cuando `INCIDENCE=1`

2) **Incidencias (tabla)** *(opcional)*  
Por defecto desactivada. Si se activa y hay incidencias, genera una tabla con:
- `PT_ID`, `ROUTE_ID`, `PK`, `M`, `DIST_AXIS`, `INC_TYPE`

#### Incidencias (INC_TYPE)

- `BAD_GEOMETRY`: geometría vacía/no válida/no puntual
- `NO_ROUTE`: falta ROUTE_ID o no existe en la capa de líneas (si se restringe por ruta)
- `NO_M_VALUES`: no se encuentran segmentos con valores M utilizables
- `TOO_FAR`: existe proyección, pero supera la distancia máxima
- `NO_MATCH`: no se pudo obtener una proyección válida con M

#### Recomendación

Para que `DIST_AXIS` esté en metros y el umbral “Distancia máxima” tenga sentido, usa un **CRS proyectado** (no geográfico).


### 8.2. Calibrate lines from distance

**Qué hace**  
Calibra el valor **M** de una capa de líneas en función de la **distancia acumulada al origen**.

El algoritmo puede trabajar de dos formas:

- **Por feature** (modo simple):  
  Cada línea se calibra de forma independiente, empezando en `Start M`.

- **Por ROUTE_ID** (modo avanzado):  
  Las líneas se calibran de forma **consistente por ruta**, manteniendo las features originales pero usando una **referencia común por ROUTE_ID**.  
  Cada vértice se proyecta sobre el tramo más cercano de dicha referencia para obtener su distancia acumulada.

> ⚠️ **Advertencia**  
> En vías con **bucles**, geometrías complejas o **ejes paralelos muy próximos**, la proyección al tramo “más cercano” puede producir asignaciones no deseadas.  
> En estos casos, se recomienda revisar el resultado o calibrar por feature.

#### Entradas

- **Capa de líneas**  
  Capa vectorial de líneas, con o sin valores M previos.

- **Start M**  
  Valor inicial de la calibración (en las unidades de salida).

- **Unidades del campo M (salida)**  
  Metros o kilómetros.

#### Opciones

- **Agrupar por ROUTE_ID**  
  Calibra por ruta manteniendo las features originales.  
  Requiere indicar el campo `ROUTE_ID` en la capa de líneas.

- **Sobrescribir M existente**  
  - **Activado**: recalibra incluso geometrías que ya tengan valores M.  
  - **Desactivado** (por defecto): las features que ya tienen M se omiten y se marcan con un *warning* (`SKIPPED_HAS_M`).

- **Invertir sentido**  
  Hace que el valor M **decrezca** en el sentido geométrico de la línea.

- **Modo de longitud** (avanzado)  
  - **Auto (recomendado)**:  
    - CRS proyectado → distancias planas  
    - CRS geográfico → cálculo geodésico usando un CRS proyectado local
  - **Planar**: fuerza distancias planas
  - **Geodesic**: fuerza cálculo geodésico

#### Salidas

- **Capa de líneas con geometría M** (`LineStringM / MultiLineStringM`)

Campos añadidos:

- `M_START` – valor M inicial de la feature
- `M_END` – valor M final de la feature
- `LEN_M` – longitud de la feature en las unidades M de salida
- `STATUS` – estado del proceso

Los atributos originales de la capa se conservan.

#### STATUS y mensajes en el log

El campo `STATUS` y el log de Processing permiten identificar el resultado de cada feature:

- **`OK`**  
  Calibración correcta.

- **`SKIPPED_HAS_M`** (*warning*)  
  La feature ya tenía valores M y no se sobrescribió.

- **`ZERO_LENGTH`** (*warning*)  
  La geometría tiene longitud cero; `M_START = M_END = Start M`.

- **`BAD_GEOMETRY`** (*critical*)  
  Geometría vacía, inválida o no lineal.

- **`NO_ROUTE`** (*critical*)  
  Falta `ROUTE_ID` cuando se ha activado la calibración por ruta.

Los *warnings* y *criticals* se reportan explícitamente en el **log de Processing**, junto con un resumen final por tipo.

#### Recomendaciones

- Usa un **CRS proyectado en metros** siempre que sea posible.
- En CRS geográfico, mantén el modo **Auto** o **Geodesic**.
- Para capas complejas:
  - revisa visualmente el resultado,
  - considera calibrar por feature en lugar de por ROUTE_ID.
 
---

 ### 8.3. Modify M geometry

**Qué hace**  
Modifica los valores **M** existentes en una capa `LineStringM / MultiLineStringM` mediante operaciones típicas de recalibración.  
No cambia X/Y (ni Z); solo reescribe **M** en los vértices.

Está pensado para:
- corregir errores de calibración,
- convertir unidades,
- invertir el sentido kilométrico,
- normalizar orígenes,
- limpiar pequeños “rebotes” en el campo M.

#### Entradas

- **Capa de líneas con M** (`LineStringM / MultiLineStringM`)

#### Operaciones

El algoritmo aplica las operaciones en este orden general: **Factor/Offset → (opcional) Invertir → (opcional) Fijar origen → (opcional) Clamp → (opcional) Monotonía**.

- **Offset**  
  Desplaza todos los M una cantidad constante: `M' = M + offset`. 

- **Factor**  
  Escala todos los M: `M' = M * factor`.  
  Útil para conversión de unidades o recalibración (ej.: km ↔ m).

- **Invertir M (manteniendo rango)**  
  Invierte el sentido de M sin cambiar el rango, usando: `M' = (Mmin + Mmax) − M`.  
  El rango `Mmin/Mmax` se calcula por **feature** o por **ROUTE_ID** según el ámbito.

- **Fijar origen (TARGET_START)**  
  Aplica un desplazamiento uniforme para que el **primer vértice** tenga el M indicado, manteniendo el resto consistente.  
  En ámbito por `ROUTE_ID`, el origen se calcula de forma común para todas las features de la ruta.

- **Recortar (Clamp) al rango [min, max]**  
  Limita M a un intervalo mínimo/máximo:  
  - si `M < min` → `M = min`  
  - si `M > max` → `M = max`  
  Sirve para limpiar valores **fuera de rango** sin “remapear” la calibración (no comprime ni estira).  
  Puede crear tramos **planos** al inicio o al final.

- **Forzar monotonía (limpieza de “rebotes”)**  
  Corrige inversiones locales de M para que evolucione siempre en el mismo sentido:  
  - **creciente**: asegura `M[i] >= M[i-1]`  
  - **decreciente**: asegura `M[i] <= M[i-1]`  
  La **tolerancia (epsilon)** permite ignorar pequeñas variaciones numéricas antes de corregir.  
  Esta opción puede “aplanar” pequeños tramos.

#### Validación

- **Requerir M** (avanzado)  
  - Activado: las features sin M generan *critical* (`NO_M_VALUES`).  
  - Desactivado: se omiten con *warning* (`SKIPPED_NO_M`).

#### Ámbito (avanzado)

- **Por feature** (recomendado)  
  Aplica operaciones usando el rango/origen de cada feature.

- **Por ROUTE_ID**  
  Usa un rango/origen común para varias features (útil si una ruta está partida).  
  Requiere indicar el campo **ROUTE_ID**.


#### Salidas

- **Capa de líneas con M modificado**.  
- Se añade el campo `STATUS` (p.ej. `OK`, `SKIPPED_NO_M`, `NO_M_VALUES`, `BAD_GEOMETRY`, `NO_ROUTE`).  
- El log de Processing incluye **warnings** y **criticals** (p.ej. `CLAMP_APPLIED`, `MONO_APPLIED`).

#### Consejo

Si usas **Invertir** o **Fijar origen** en rutas partidas, considera el ámbito **Por ROUTE_ID** para mantener coherencia global.


---

## 9. Licencia

Este proyecto se distribuye bajo la **GNU General Public License v3.0 (GPL-3.0)**.  
Puedes usarlo, modificarlo y compartirlo libremente bajo los términos de esta licencia.

---

## 10. Autor

- **LinkedIn**: [Javi H. Piris](https://www.linkedin.com/in/javierhpiris)  
- **GitHub**: [@Javisionario](https://github.com/Javisionario)
