![BANNER](IMAGES/BANNER.png)
## L-RAT (Linear Referencing & Analysis Tools) — QGIS Processing Plugin
[En español](README.md)

L-RAT is a **QGIS Processing plugin** focused on **road engineering and linear-infrastructure workflows**. It includes tools for:

- **Calibration (Chainage/PK)**: add/derive chainage values (PK) in geometries (compute chainage point by proximity; generate/adjust M values on lines).
- **Linear referencing (Chainage/PK)**: locate and extract **points** and **segments** along calibrated lines (`LineStringM / MultiLineStringM`).
- **Profiles & slopes**: compute longitudinal profile and slopes from an axis and a DEM, and generate plots/outputs for mapping (no chainage/PK needed).
- **Misc**: geometric analysis on linear layers (e.g., curve detection and curvature centers).

Repository: https://github.com/Javisionario/L-RAT

---

# Table of Contents

- [1. Requirements and concepts](#1-requirements-and-concepts)
  - [1.1. Supported data types and geometries](#11-supported-data-types-and-geometries)
  - [1.2. What is an M geometry?](#12-what-is-an-m-geometry)
  - [1.3. Units of the M value](#13-units-of-the-m-value)
  - [1.4. ROUTE_ID field (Road/route identifier)](#14-route_id-field-roadroute-identifier)
  - [1.5. Accepted PK formats](#15-accepted-pk-formats)
  - [1.6. Issue handling](#16-issue-handling)

- [2. Installation](#2-installation)
- [3. Plugin structure](#3-plugin-structure)

- [4. Calibrate M geometry](#4-calibrate-m-geometry)
  - [4.1. Issues and traceability](#41-issues-and-traceability)
  - [4.2. Calibrate lines from distance](#42-calibrate-lines-from-distance)
  - [4.3. Calibrate lines from points/events](#43-calibrate-lines-from-pointsevents)
  - [4.4. Calibrate points](#44-calibrate-points)
  - [4.5. Edit calibration (Modify M values)](#45-edit-calibration-modify-m-values)

- [5. Locate points (requires M geometry)](#5-locate-points-requires-m-geometry)
  - [5.1. Issues and traceability](#51-issues-and-traceability)
  - [5.2. Out-of-range and M-coverage discontinuity adjustments](#52-out-of-range-and-m-coverage-discontinuity-adjustments)
  - [5.3. Locate points](#53-locate-points)
  - [5.4. Locate points from table](#54-locate-points-from-table)

- [6. Locate segments (requires M geometry)](#6-locate-segments-requires-m-geometry)
  - [6.1. Issues and traceability](#61-issues-and-traceability)
  - [6.2. Out-of-range and M-coverage discontinuity adjustments](#62-out-of-range-and-m-coverage-discontinuity-adjustments)
  - [6.3. Locate segments](#63-locate-segments)
  - [6.4. Locate segments from points/events (PK) table](#64-locate-segments-from-pointsevents-pk-table)
  - [6.5. Locate segments from segment table](#65-locate-segments-from-segment-table)

- [7. Miscellaneous](#7-miscellaneous)
  - [7.1. Curve Detection and Curvature Centers](#71-curve-detection-and-curvature-centers)

- [8. Profile & Slope](#8-profile--slope)
  - [8.1. Issues and traceability](#81-issues-and-traceability)
  - [8.2. Profile Slope Plotter](#82-profile-slope-plotter)
  - [8.3. Slope and Longitudinal Profile](#83-slope-and-longitudinal-profile)

- [9. License](#9-license)
- [10. Author](#10-author)

---

# 1. Requirements and concepts

## 1.1. Supported data types and geometries

**Linear geometries**
- `LineString` / `MultiLineString`
- `LineStringM` / `MultiLineStringM` (lines with an **M** value per vertex)
- Optional `Z` (preserved when applicable)

**Point geometries**
- `Point` / `MultiPoint` (events, milestones, samples)

**Recommended CRS**
- For measurements and distances: a **projected CRS** (meters).

---

## 1.2. What is an M geometry?

`LineStringM / MultiLineStringM` layers store, in addition to X/Y (and optionally Z), an **M** value per vertex. In road contexts, M is commonly used as kilometer point / chainage (PK), enabling you to:
- **Interpolate** a position along a line from a chainage value (PK).
- **Extract** segments between a chainage start and end.
- **Calibrate** points by proximity to an M-enabled network.

**L-RAT** provides tools to perform these workflows.

---

## 1.3. Units of the M value

L-RAT algorithms include **M unit** parameters (meters or kilometers).  
An incorrect configuration will produce wrong results (e.g., shifted points or inconsistent lengths).

---

## 1.4. ROUTE_ID field (Road/route identifier)

Many algorithms work **per route/road** using an identifier (typically `ROUTE_ID`):
- A linear layer (axis/road centerline) with `ROUTE_ID`.
- An events layer/table (points or segments) referencing the same `ROUTE_ID`.

If a road is split into multiple features, L-RAT can:
- work **per feature**, or
- **group by ROUTE_ID** (when the option exists), keeping global consistency.

In dense networks or with parallel roads, **restricting by ROUTE_ID** is often essential to avoid wrong matches.

---

## 1.5. Accepted PK formats

L-RAT tries to be flexible with PK input and accepts common formats:
- `km+mmm` (e.g., `12+345`)
- decimal kilometers (e.g., `12.345`, `3,05`)
- PK in **meters** (depending on algorithm and unit options)

---

## 1.6. Issue handling

L-RAT can log issues to audit adjustments, warnings, and errors:
- in **output fields** (e.g., `ADJUSTED`, `ADJUST_REASON`, `STATUS`),
- in an optional **issues table** (created only if enabled and if issues exist),
- and in the **Processing log**.

> NOTE: Details (exact fields and codes) are documented **per subgroup** in 4.1 / 5.1 / 6.1, because they differ slightly between algorithm families.

---

# 2. Installation

- **From the official QGIS plugin repository (recommended)**: when published, install it via **QGIS → Plugins → Manage and Install Plugins** → All → search for `L-RAT`.
- **From ZIP/GitHub** (development):
  1. Download the repository ZIP.
  2. **QGIS → Plugins → Manage and Install Plugins** → Install from ZIP → select the downloaded ZIP.

---

# 3. Plugin structure

Algorithms appear in the **Processing Toolbox** under these subgroups:

- **Calibrate M Geometry**
- **Locate points (requires M geometry)**
- **Locate segments (requires M geometry)**
- **Miscellaneous**
- **Profile & Slope**

![MENU](IMAGES/MENU.png)

---

# 4. Calibrate M geometry

This subgroup provides tools to **calibrate** chainage (PK) values to prepare data for linear-referencing workflows. These algorithms add or edit calibration values on existing geometries.

![CALIBRATE](IMAGES/CALIBRATE.PNG)

---

## 4.1. Issues and traceability

In this subgroup, traceability relies on **control fields** in outputs and, in some algorithms, an optional **issues table**.

**Traceability mechanisms (depending on the algorithm):**
- **Line output with `STATUS` (string)**:
  - *Calibrate lines from distance*
  - *Calibrate lines from points (events)*
  - *Modify M geometry*
- **Point output with `INCIDENCE` (0/1) + `INC_TYPE` (string)**:
  - *Calibrate points*
- **Optional issues table (`OUTPUT_ISSUES`, no geometry)** *(only for some algorithms)*:
  - *Calibrate lines from points (events)*
  - *Calibrate points*

  The issues table is created **only if** the user enables the option **and** there are **actual issue rows**.

**Typical behavior when issues occur:**
- Line algorithms usually **return one output feature per input feature**. If calibration cannot be done correctly, `STATUS` describes the reason and control fields may be `NULL`.
- In *Calibrate points*, every point is always present in the final layer, but:
  - `INCIDENCE = 1` indicates an issue,
  - `INC_TYPE` identifies the reason,
  - some fields (`PK`, `M`, `DIST_AXIS`) may be `NULL` depending on the case.

---

## 4.2. Calibrate lines from distance

Calibrates the **M** value of a line layer based on **cumulative distance from an origin**. The result is a calibrated `LineStringM` / `MultiLineStringM` layer (preserving Z if present), calibrated by distance from origin (per feature or per route).

### Inputs
- **Line layer to calibrate** (with or without M).

### Parameters
- **M units (output)**: `Meters (m)` / `Kilometers (km)`.
- **Start M value**: initial M value (in output units).
- **Reverse direction**: M decreases along the geometry direction.
- **Overwrite existing M**: if disabled and geometry already has M, it is skipped and flagged in `STATUS`.
- (Advanced) **Length mode**: `Auto`, `Planar`, `Geodesic` (use a local projected CRS).
  - `Auto` (recommended): uses **planar** distances if CRS is projected; if CRS is geographic, uses a **local projected CRS** to measure/locate.
  - `Planar`: forces planar distances in the layer CRS.
  - `Geodesic`: forces calculations using a local projected CRS.

### Output
- **Calibrated lines** (`LineStringM/MultiLineStringM`).
- Added fields (in addition to original fields):
  - `M_START` (double)
  - `M_END` (double)
  - `LEN_M` (double) — length in M units (per configuration)
  - `STATUS` (string)
  - `N_SEGS` (integer) — number of segments/parts in the original feature. Recommended to verify calibration continuity (M values) when `N_SEGS > 1` (MultiLineString geometries).

### Issue codes
`STATUS` can take the following values:
- `OK`: calibrated successfully.
- `SKIPPED_HAS_M`: not recalibrated because it already had M and **Overwrite existing M** is disabled.
- `BAD_GEOMETRY`: empty/invalid/non-linear geometry.
- `ZERO_LENGTH`: length 0 (warning).
- `NO_REFERENCE_GEOM`: failed to build/get a valid reference geometry for that route (route mode only).

<p><b>Note:</b> If the input geometry is multipart (MultiLineString), each part is processed separately and the output will contain one feature per part.</p>

---

## 4.3. Calibrate lines from points/events

Calibrates the **M** field of a line layer with chainage (PK) stored in a **points/events layer**.  
Each point is projected onto the nearest line (or onto the corresponding route if restricted by `ROUTE_ID`) and used as a **control point** to interpolate and assign M along the axis. The result is a `LineStringM / MultiLineStringM` layer calibrated with M values according to the provided chainage (PK).

### Inputs
- **Line layer** (with or without M): linear layer to calibrate (with or without existing M values).
- **Reference point layer**: events with known chainage (PK) values.
- **Chainage (PK) field**: field containing those values.

### Parameters
- **Input chainage (PK) units (point field)**:
  - `Auto ('PK+mmm' text or infer units: m / km)`
  - `Meters (m)`
  - `Kilometers (km)`
- **M units (output)**: `Meters (m)` / `Kilometers (km)`
- **Maximum distance (search / projection)**  
  Maximum threshold to accept point–line matching. If the closest match is farther than this, the point is flagged as an issue (`TOO_FAR`) and does not contribute to calibration.
- **Endpoint snap tolerance (to build route reference)**  
  Tolerance used while building the `ROUTE_ID` reference (when grouping by route).

### Options
- **Group by ROUTE_ID**: (Requires specifying the `ROUTE_ID` field in both points and lines). Forces each point to calibrate only the line with the same route identifier. Also, the algorithm automatically detects forks and parallel centerlines and generates `warnings` if they may affect calibration.
- **Add ROUTE_ID to output (from points)**: if output already has `ROUTE_ID`, creates `ROUTE_ID_FROM_PTS` to avoid overwriting.
- **Behavior outside control range**:
  - `Linear extrapolation`: extends linearly using the slope from the extreme controls.
  - `Clamp`: fixes M to the nearest extreme control value (flat/constant outside range).
  - `Leave NULL (NaN)`: leaves M unassigned outside the range (NULL/NaN), useful to identify uncalibrated parts.

- *(Advanced)* **Factor**: `M_final = M * factor`
- *(Advanced)* **Offset**: `M_final = M + offset`

- *(Advanced)* **Generate issues table**: created only if enabled and issues exist.
- *(Advanced)* **Generate projected points**: to inspect projected point positions vs the input layer.

> Note on forks/parallel segments: the algorithm detects potentially ambiguous geometries and emits warnings for review.

### Outputs
1) **Calibrated lines** (`LineStringM / MultiLineStringM`)  
   Added fields (in addition to original fields):
   - *(Optional, if “Add ROUTE_ID to output” enabled)* `ROUTE_ID` **or** `ROUTE_ID_FROM_PTS` (string)
   - `N_CTRL` (int) — number of controls finally used for that feature (after filters)
   - `M_START` (double) — M at first vertex (can be `NULL` if NaN at start)
   - `M_END` (double) — M at last vertex (can be `NULL` if NaN at end)
   - `M_LEN` (double) — `abs(M_END - M_START)` if both exist; otherwise `NULL`
   - `LEN_GEOM` (double) — geometric line length (in CRS units)
   - `LEN_ERR_M` (double) — `abs(LEN_GEOM - M_LEN_in_meters)` if `M_LEN` exists
   - `LEN_ERR_P` (double) — percent error vs `LEN_GEOM` if `M_LEN` exists and `LEN_GEOM>0`
   - `HAS_NULLM` (int) — 1 if any vertex ended with M = `NaN` (when “Leave NULL” outside range)
   - `STATUS` (string)
   - `N_SEGS` (integer) — number of segments/parts in the original feature. Recommended to verify calibration continuity (M values) when `N_SEGS > 1` (MultiLineString geometries).

> Note: `LEN_GEOM` (and therefore `LEN_ERR_M`/`LEN_ERR_P`) is meaningful only in a projected CRS (meters). In a geographic CRS (degrees) these values are not comparable.

2) **Projected points** *(optional, if enabled)*  
   Fields:
   - `PT_ID` (string)
   - `LINE_FID` (int)
   - `ROUTE_ID_LINE` (string)
   - `ROUTE_ID_PTS` (string)
   - `PK_RAW` (string)
   - `M` (double)
   - `DIST_AXIS` (double)
   - `DIST_ALONG` (double)

3) **Issues (table)** *(optional, if enabled and has rows)*  
   Fields:
   - `INC_TYPE` (string)
   - `PT_ID` (string)
   - `ROUTE_ID` (string)
   - `PK_RAW` (string)
   - `LINE_FID` (int)
   - `DIST_AXIS` (double)
   - `DIST_ALONG` (double)
   - `NOTE` (string)

#### `STATUS` (line output)
- `OK`: calibrated successfully.
- `BAD_GEOMETRY`: invalid or inconsistent geometry during processing.
- `NO_ROUTE_REFERENCE`: failed to build a valid route reference (ROUTE_ID mode).
- `INSUFFICIENT_POINTS`: not enough valid controls to calibrate that feature/route.

#### `INC_TYPE` (issues table and/or typical warnings)
- **Typical point-related issues (critical for that point):**
  - `BAD_GEOMETRY`: invalid/empty/non-point geometry.
  - `PK_INVALID`: PK could not be parsed/converted.
  - `NO_ROUTE`: missing `ROUTE_ID` on point or not present in lines (if route restriction enabled).
  - `TOO_FAR`: a candidate line exists, but the closest exceeds `Max distance`.
  - `NO_MATCH`: failed to obtain a valid match.
  - `INSUFFICIENT_POINTS`: not enough valid controls to calibrate the route/feature.
  - `NO_ROUTE_REFERENCE`: failed to build a valid reference for the route (group by `ROUTE_ID`).
- **Quality-review warnings:**
  - `DUPLICATE_POS_IGNORED`: multiple PKs project to the same position on the reference → duplicates ignored.
  - `NON_MONOTONIC_PK`: PKs do not follow spatial order along the axis (possible PK or matching error).
  - `ROUTE_TOPOLOGY_WARNING`: route reference shows suspicious topology (gaps/order) and should be reviewed.

---

## 4.4. Calibrate points

Assigns a **chainage** value (PK) to each point from the **interpolated M** value, by projecting the point onto a calibrated line layer (`LineStringM / MultiLineStringM`). It does not change point geometry: it adds calibration fields to attributes.

### Inputs
- **Point layer to calibrate**
- **Calibrated line layer (M)**

### Parameters
- **M units**: m or km
- **Maximum distance (search/projection)**: threshold to accept matching (if the nearest axis is farther, an issue is flagged)

### Options
- **Restrict matching by ROUTE_ID** (requires `ROUTE_ID` in points and lines)  
  Searches only within the same route/road. *(Recommended if there are parallel roads.)*
- **Add ROUTE_ID to output (from lines)** (if it already exists, creates `ROUTE_ID_MATCH`)  
  Copies the matched line identifier to the point.
- **Generate issues table (only if any)** *(advanced)*

### Outputs
1) **Calibrated points** (original attributes +)
- *(Optional if enabled)* `ROUTE_ID` **or** `ROUTE_ID_MATCH` (string)
- `PK` (string) — chainage formatted as `km+mmm`
- `M` (double)
- `DIST_AXIS` (double)
- `INCIDENCE` (int) — 0/1
- `INC_TYPE` (string)

2) **Issues (table)** *(optional)*  
Fields: `PT_ID`, `ROUTE_ID`, `PK`, `M`, `DIST_AXIS`, `INC_TYPE`

### `INC_TYPE` codes
- `BAD_GEOMETRY`: empty/invalid/non-point geometry.
- `NO_ROUTE`: missing `ROUTE_ID` or not present in the line layer (if restricted by route).
- `NO_M_VALUES`: no usable M values found in candidate lines.
- `TOO_FAR`: a projection with M exists, but exceeds max distance.
- `NO_MATCH`: failed to obtain a final valid projection.

---

## 4.5. Edit calibration (Modify M values)

Modifies existing **M** values in a `LineStringM / MultiLineStringM` layer using common recalibration operations.  
It does not change `X`/`Y` (or `Z`); it only rewrites **M**.

Intended for:
- Fixing calibration errors
- Converting units
- Reversing chainage direction
- Normalizing origins
- Cleaning small “bounces” in M values

### Inputs
- **Line layer with M** (`LineStringM / MultiLineStringM`)

### Operations
Operations are applied in this general order:
**Factor/Offset → Reverse → Set origin → Clamp → Monotonicity**.

- **Offset**: shift all M values by a constant: `M' = M + offset`
- **Factor**: scale all M values. Useful for unit conversion or recalibration (e.g., km ↔ m): `M' = M * factor`
- **Reverse M (preserving range)**: reverse direction without changing the range: `M' = (Mmin + Mmax) − M`
- **Set origin (TARGET_START)**: apply a uniform shift so the first vertex matches the target M, keeping consistency from `TARGET_START`
- **Clamp to [min, max]**: limit M to a min/max range. Useful to clean out-of-range values without “remapping” calibration (does not compress/stretch). May create flat segments at start/end.
- **Force monotonicity**: fix “bounces” so M is always increasing or decreasing (with `epsilon`)

### Validation
- **Require M** (advanced): if enabled, geometries without M are flagged with `STATUS=NO_M_VALUES`. If not required, they are skipped with `STATUS=SKIPPED_NO_M`.

### Scope (advanced)
- **Per feature** (recommended): operations use each feature’s own range/origin.
- **Per ROUTE_ID** (requires `ROUTE_ID`): uses a common range/origin across multiple features (useful for split routes).

### Output
- Layer with modified M + `STATUS`
  - `OK`: modified successfully (or no change needed).
  - `SKIPPED_NO_M`: feature has no M; skipped (if M not required).
  - `NO_M_VALUES`: no usable M values found to operate (includes no-M cases).
  - `BAD_GEOMETRY`: invalid/non-linear geometry.
  - `NO_ROUTE`: missing valid `ROUTE_ID` (if scope is per route).

---

# 5. Locate points (requires M geometry)

This subgroup locates (interpolates) points along an **M-calibrated** network/axis with valid M values (`LineStringM / MultiLineStringM`) from `ROUTE_ID` + `chainage/PK`.

![LOCATE](IMAGES/LOCATE.PNG)

### Common parameters

- **Calibrated line layer** (`LineStringM / MultiLineStringM`): network/axis with M values.
- **ROUTE_ID field**: route index field `ROUTE_ID` (string).
- **M units** (km or m)

**Common advanced options:**
- **Tolerance (km)**: fixes small mismatches within calibrated coverage (rounding or imperfect calibration). **Does not fix coverage discontinuities**; it only helps when chainage (PK) falls very close to an existing/interpolable value.
- **Adjust to nearest available chainage point (PK)**: controls what happens when the chainage (PK) falls into an **M-coverage discontinuity** (due to incomplete geometry or M gaps):
  - Enabled → the point can be created by snapping to the nearest **covered** chainage (PK) (`ADJUST_REASON=GAP_SNAP`).
  - Disabled → the event becomes **critical** (`NO_MATCH`) and the point is not created.
- **Generate issues table (adjustments/critical)**  
  If enabled, the table is created **only if** there are actual issue rows (adjustments or critical events).

---

## 5.1. Issues and traceability

Traceability in this subgroup is based on:
- Control fields in point outputs: `PK_REQ`, `PK`, `ADJUSTED`, `ADJUST_REASON`, `STATUS`.
- An optional **Issues (table)** with fields: `ADJUSTED`, `ADJUST_REASON`, `WARNINGS`, `CRITICALS`.

### Point output (control fields)
- `PK_REQ`: requested chainage (PK) (formatted `km+mmm`).
- `PK`: final chainage (PK) used (may be adjusted).
- `ADJUSTED`: `0/1`.
- `ADJUST_REASON`: reasons separated by `;` (e.g., `OUT_OF_RANGE;GAP_SNAP`).
- `STATUS`: for generated points, the algorithm writes `OK`.

### Issues table (optional)
- Created only if enabled **and** there are actual rows (adjustments and/or criticals).
- Records:
  - **Adjustments**: row with `ADJUSTED=1`, `ADJUST_REASON=...`, `CRITICALS` empty.
  - **Criticals**: row with `CRITICALS` filled (e.g., `NO_ROUTE`, `PK_INVALID`, `NO_M_RANGE`, `NO_MATCH`).

### Adjustment codes (`ADJUST_REASON`)
- `OUT_OF_RANGE`  
  Requested chainage (PK) is outside the route’s global `[M_min, M_max]` range and is clamped to the nearest endpoint.
- `GAP_SNAP`  
  Requested chainage (PK) falls inside the global range but in an **M-coverage discontinuity**; if adjustment is enabled, it snaps to the nearest **covered** PK.

### Critical error codes (`CRITICALS`)
- `NO_ROUTE`: `ROUTE_ID` does not exist in the line layer or could not be indexed.
- `NO_M_RANGE`: failed to compute a valid M range for that route.
- `NO_MATCH`: failed to interpolate the point (includes discontinuity with snap disabled).

> Note about `PK_INVALID`:
> - In **Locate points from table**, `PK_INVALID` can appear as a critical **per row** (chainage (PK) not parseable).
> - In **Locate points (manual)**, an invalid chainage (PK) input triggers a **validation error** and the algorithm stops (it is not reported as a per-event critical).

---

## 5.2. Out-of-range and M-coverage discontinuity adjustments

In segmented networks (multiple features per `ROUTE_ID`, or non-continuous M calibration), **discontinuities** may exist—areas not covered for certain chainage (PK) values, due to split geometries or M coverage gaps.

L-RAT distinguishes two situations:

### 1) Global out of range (`OUT_OF_RANGE`)
Requested chainage (PK) is outside the route’s global available range (`M_min–M_max`, considering all geometries for the `ROUTE_ID`).  
The algorithm clamps chainage (PK) to the nearest endpoint and marks:
- `ADJUSTED=1`
- `ADJUST_REASON` includes `OUT_OF_RANGE`

### 2) M-coverage discontinuity (`GAP_SNAP`)
Requested chainage (PK) is within the global range, but **no segment** of that route covers it (not interpolable in any available `LineStringM`).  
If **Adjust to nearest available chainage (PK)…** is enabled, the algorithm:
- finds the nearest **covered** chainage (PK),
- adjusts to it,
- marks `ADJUST_REASON` with `GAP_SNAP`.

If the option is disabled and requested chainage values (PK) falls in a discontinuity:
- the event is marked as a **critical** `NO_MATCH` and **no point** is created.

> A single event may accumulate both reasons. For example, an out-of-range chainage (PK) is clamped to an endpoint (`OUT_OF_RANGE`) and if that endpoint is not covered by any segment, `GAP_SNAP` may also apply.

---

## 5.3. Locate points

Locates (interpolates) **1 or 2 points** on a calibrated line layer from manually entered `ROUTE_ID` + `Chainage/PK` (and optionally an additional ID).

### Inputs
- **Calibrated line layer (M)** + **ROUTE_ID field**.
- **M units**.
- **Point 1 (required)**:
  - `ROUTE_ID (point 1)`
  - `Chainage (PK) (point 1)`
  - `Additional point ID (point 1)` *(optional)*
- **Point 2 (optional)**:
  - `Define point 2` (switch)
  - `ROUTE_ID (point 2)`
  - `Chainage (PK) (point 2)`
  - `Additional point ID (point 2)` *(optional)*
 
> Chainage (PK) format: `km+mmm` or decimal km (e.g., `12+345`, `12.345`, `3,05`).

### Outputs
1) **Located points** (point layer)
- `ROUTE_ID` (string)
- `EVENT_ID` (string) *(only if any additional ID was provided)*
- `PK_REQ` (string): requested chainage (PK)
- `PK` (string): located chainage (PK)
- `ADJUSTED` (int)
- `ADJUST_REASON` (string)
- `STATUS` (string) → `OK` on generated points

2) **Issues (table)** *(optional; only if there are rows)*
- `ROUTE_ID`
- `EVENT_ID` *(only if present in output)*
- `PK_REQ`
- `ADJUSTED`, `ADJUST_REASON`
- `WARNINGS`, `CRITICALS`

> For `ADJUST_REASON`, `WARNINGS`, and `CRITICALS`, see [5.1. Issues and traceability](#51-issues-and-traceability).

---

## 5.4. Locate points from table

Locates points using an events table/layer: each row defines a point via `ROUTE_ID` + `Chainage/PK` (and optionally an additional ID).

### Inputs
- **Calibrated line layer (M)** + `ROUTE_ID` field.
- **Events table** (each row = 1 point) with:
  - `ROUTE_ID field in the table`
  - `Chainage (PK) field in the table` [km+mmm] or decimal (km)
  - `Additional Point ID field` *(optional)* (PK_ID)

> Chainage (PK) format: `km+mmm` or decimal km (e.g., `12+345`, `12.345`, `3,05`).

### Specific options
- **Add table fields to output**  
  If enabled, copies all table fields into the output.  
  If name collisions exist, suffix `_TBL` is added to copied fields.

### Outputs
1) **Located points** (point layer)
- `ROUTE_ID` (string)
- `PK_ID` (string) *(only if “Event ID field” was configured)*
- `PK_REQ`, `PK`, `ADJUSTED`, `ADJUST_REASON`, `STATUS`
- *(Optional)* table fields (if enabled; with `_TBL` if colliding)

2) **Issues (table)** *(optional; only if there are rows)*
- `ROUTE_ID`
- `PK_ID` *(only if configured)*
- `PK_REQ`
- `ADJUSTED`, `ADJUST_REASON`
- `WARNINGS`, `CRITICALS`

> For `ADJUST_REASON`, `WARNINGS`, and `CRITICALS`, see [5.1. Issues and traceability](#51-issues-and-traceability).

---

# 6. Locate segments (requires M geometry)

This subgroup extracts **segments** (lines) defined by `ROUTE_ID` + chainage (PK) (`PK_START` + `PK_END`) from an **M-calibrated** network/axis with valid M values (`LineStringM / MultiLineStringM`).

![EXTRACT](IMAGES/EXTRACT.PNG)

### Common parameters

- **Calibrated line layer (M)** (`LineStringM / MultiLineStringM`) and its **ROUTE_ID field** used to identify the road/route to extract.
- **M units**: `Meters (m)` / `Kilometers (km)`.
- **Generate endpoint points** *(optional)*: creates 2 points per segment (start/end) with requested `PK_REQ` and final `PK`.

**Common advanced options:**
- **Tolerance (km) for M snap/rounding**: helps resolve small mismatches within calibrated coverage (**does not fix coverage discontinuities**).
- **Adjust to nearest available chainage point (PK)**: controls what happens when a PK falls into a **discontinuity** (incomplete geometry or M gaps):
  - Enabled → the endpoint can be adjusted to the nearest covered chainage (PK) (`ADJUST_REASON=GAP_SNAP`).
  - Disabled → the event becomes **critical** (`NO_MATCH`) and no geometry is produced.
- **Generate issues table if any (adjustments/warnings/criticals)**: if enabled, records event/group issues when they exist (depending on algorithm).

---

## 6.1. Issues and traceability

Traceability in this subgroup relies on:
- **Control fields** in segment outputs.
- Optional **endpoint points** with per-endpoint fields.
- Optional **Issues (table)** (no geometry) with `WARNINGS` and `CRITICALS` (and, depending on the algorithm, identifier fields).

### Control fields (segment output)
- `PK_INI` / `PK_FIN` (string): chainage (PK) values finally used to extract the segment (`km+mmm`).
- `DIST_PK_KM` (double): chainage (PK) distance in km between `PK_INI` and `PK_FIN`.
- `DIST_GEOM_KM` (double): geometric length of the extracted segment(s), in km.
- `ADJUSTED` (int): `0/1`. Indicates whether any adjustment was applied.
- `ADJUST_REASON` (string): reasons separated by `;` (e.g., `OUT_OF_RANGE;GAP_SNAP`).
- `N_PIECES` (int): number of generated pieces. If `N_PIECES > 1`, warning `SEGMENT_SPLIT` is recorded.
- `STATUS` (string): status of the generated segment (`OK` for created segments; critical events **produce no geometry**).

### Endpoint points (optional)
Creates 2 points per segment:
- `PK_REQ` (string): requested chainage (PK) for that endpoint.
- `PK` (string): final chainage (PK) after adjustments.
- `ADJUSTED` (int), `ADJUST_REASON` (string).
- Plus identifiers (depending on algorithm): `SEG_ID`, `PAIR_ID` or `EVENT_ID`, and `ROUTE_ID`.

### Issues table (optional)
- Created **only if** enabled and actual rows exist.
- For each event/group (depending on algorithm), records:
  - `ADJUSTED` / `ADJUST_REASON` (if adjustments occurred),
  - `WARNINGS` (coded string list),
  - `CRITICALS` (coded string list).
- **If an event is critical**: no output geometry exists, but a row is recorded in this table.

### Adjustment, warning, and critical codes

**Adjustments (`ADJUST_REASON`)**
- `OUT_OF_RANGE`: An endpoint is outside the route’s global available range (`M_min–M_max`, considering all segments) and is clamped to the nearest endpoint.
- `GAP_SNAP`: Requested chainage (PK) is within the global range but falls into an **M-coverage discontinuity** (not interpolable in any available segment).  
  If adjustment is enabled, the nearest covered chainage (PK) is used.

**Warnings (`WARNINGS`)**
- `SEGMENT_SPLIT`: Segment generated into multiple pieces (`N_PIECES > 1`).
- `ODD_PK_IGNORED` *(only in* **Locate segments from points/events table***)*: In a `(ROUTE_ID, PAIR_ID)` group, an unpaired event is ignored.

**Criticals (`CRITICALS`) — segment is not generated**
- `NO_ROUTE`: Requested `ROUTE_ID` does not exist (or is empty/null) in the indexed line layer.
- `PK_INVALID`: Some chainage (PK) is not parseable/convertible (e.g., non-parseable `km+mmm` text or decimal km).
- `NO_M_RANGE`: Failed to compute a valid M range for that route (considering all segments for that `ROUTE_ID`).
- `NO_MATCH`: Failed to extract geometry for the requested segment **even if the `ROUTE_ID` exists**, typically due to:
  - endpoints in a discontinuity with `GAP_SNAP` disabled,
  - the requested portion not covered by any available `LineStringM` for that `ROUTE_ID`,
  - empty extraction after applying (or failing to apply) adjustments.

> A single event may accumulate multiple reasons (e.g., `OUT_OF_RANGE;GAP_SNAP`) if, after clamping, the endpoint is not covered by any segment.

---

## 6.2. Out-of-range and M-coverage discontinuity adjustments

In segmented networks (multiple features per `ROUTE_ID`, or non-continuous M calibration), **uncovered zones** may exist for certain PK values. L-RAT distinguishes:

### 1) Global out of range (`OUT_OF_RANGE`)
Requested chainage (PK) is outside the route’s global available range (`M_min–M_max`, considering all geometries for the `ROUTE_ID`).  
The algorithm clamps to the nearest endpoint and marks:
- `ADJUSTED=1`
- `ADJUST_REASON` includes `OUT_OF_RANGE`

### 2) M-coverage discontinuity (`GAP_SNAP`)
Requested chainage (PK) is within the global range, but **no segment** covers it (not interpolable in any available `LineStringM`).  
If **Adjust to nearest available chainage point (PK)…** is enabled, the algorithm:
- finds the nearest **covered** chainage (PK),
- adjusts the endpoint to it,
- marks `ADJUST_REASON` with `GAP_SNAP`.

If the option is disabled and an endpoint falls in a discontinuity:
- the event is marked as a **critical** `NO_MATCH` and **no segment** is generated.

---

## 6.3. Locate segments

Extracts **1 or 2 segments** on a calibrated line layer from manually entered `ROUTE_ID` + chainage (PK) (`PK_INI` + `PK_FIN`)

### Inputs
- **Calibrated line layer (M)**
- **Route identifier field** `ROUTE_ID`
- **M units**
- **Segment 1 (required)**:
  - `Route identifier (segment 1)`
  - `Start chainage (PK) (segment 1)`
  - `End chainage (PK) (segment 1)`
  - `OAdditional segment ID (SEG_ID) (segment 1)` *(optional)*
- **Segment 2 (optional)**:
  - `Define second segment`
  - `Route identifier (segment 2)`
  - `Start chainage (PK) (segment 2)`
  - `End chainage (PK) (segment 2)`
  - `Additional segment ID (SEG_ID) (segment 2)` *(optional)*

> Chainage (PK) format: `km+mmm` or decimal km (e.g., `12+345`, `12.345`, `3,05`).

### Specific options
- **Generate endpoint points (optional)**: creates 2 points per segment (start/end) with `PK_REQ` vs final `PK`.

### Outputs
1) **Extracted segments** (line layer)  
Typical fields:
- `ROUTE_ID` (string)
- `SEG_ID` (string) *(only if any optional ID was provided)*
- `PK_INI`, `PK_FIN`, `DIST_PK_KM`, `DIST_GEOM_KM`, `ADJUSTED`, `ADJUST_REASON`, `N_PIECES`, `STATUS`

2) **Endpoint points (optional)**  
2 points per segment with: `ROUTE_ID`, `PK_REQ`, `PK`, `ADJUSTED`, `ADJUST_REASON` (+ `SEG_ID` if present).

3) **Issues (table)** *(optional; only if there are rows)*  
Includes `WARNINGS` and `CRITICALS` (see [6.1](#61-issues-and-traceability)).

---

## 6.4. Locate segments from points/events (PK) table

Generates segments from a table where **each row provides one chainage value (PK)**, associated with `ROUTE_ID` and `PAIR_ID`. Segments are built by pairing chainage (PK) values within each identifier group (ideally unique per segment) for optimal algorithm behavior.

#### How it works (pairing)
For each `(ROUTE_ID, PAIR_ID)`:
- Sort chainage (PK) points numerically.
- Pair sequentially: (0–1), (2–3), (4–5)...
- If one chainage (PK) point is left unpaired, it is ignored and warning `ODD_PK_IGNORED` is recorded.

### Inputs
- **Calibrated line layer (M)** and its `ROUTE_ID` field.
- **Events (PK) table** (each row = 1 chainage value (PK)):
  - `ROUTE_ID field in the table`
  - `PAIR_ID field (segment/event) in the table`
  - Chainage (PK) field in the table`
- **M units**

> Chainage (PK) format: `km+mmm` or decimal km (e.g., `12+345`, `12.345`, `3,05`).

### Specific options
- **Add table fields to output**: copies table fields to the resulting segment. If a name collides with output fields, prefix `T_` is added (e.g., `T_MY_FIELD`).
- **Generate endpoint points (optional)**

### Outputs
1) **Extracted segments** (line layer)  
Typical fields:
- `ROUTE_ID`, `PAIR_ID`
- `PK_INI`, `PK_FIN`, `DIST_PK_KM`, `DIST_GEOM_KM`
- `ADJUSTED`, `ADJUST_REASON`, `N_PIECES`, `STATUS`
- *(Optional)* copied table fields (with `T_` if colliding).

2) **Endpoint points (optional)**  
2 points per segment with: `ROUTE_ID`, `PAIR_ID`, `PK_REQ`, `PK`, `ADJUSTED`, `ADJUST_REASON`.

3) **Issues (table)** *(optional; only if there are rows)*  
Includes `WARNINGS` (e.g., `SEGMENT_SPLIT`, `ODD_PK_IGNORED`) and `CRITICALS` (see [6.1](#61-issues-and-traceability)).

---

## 6.5. Locate segments from segment table

Extracts segments from a segment table where **each row defines one segment** from `ROUTE_ID` + chainage (PK) (`PK_INI` + `PK_FIN`) and (optionally) an `EVENT_ID`.

### Inputs
- **Calibrated line layer (M)** and its `ROUTE_ID` field.
- **Segment table** (each row = 1 segment) with:
  - `ROUTE_ID field in the table`
  - `Start chainage (PK) field`
  - `End chainage (PK) field`
  - `Additional segment ID field (EVENT_ID)` *(optional)*
- **M units**

> Chainage (PK) format: `km+mmm` or decimal km (e.g., `12+345`, `12.345`, `3,05`).

### Specific options
- **Add table fields to output**: copies table fields to the resulting segment.  
  If there is a name collision with output fields, prefix `T_` is added.
- **Generate endpoint points (optional)**

### Outputs
1) **Extracted segments** (line layer)  
Typical fields:
- `ROUTE_ID`
- `EVENT_ID` *(only if configured)*
- `PK_INI`, `PK_FIN`, `DIST_PK_KM`, `DIST_GEOM_KM`
- `ADJUSTED`, `ADJUST_REASON`, `N_PIECES`, `STATUS`
- *(Optional)* copied table fields (with `T_` if colliding).

2) **Endpoint points (optional)**  
2 points per segment with: `ROUTE_ID`, `PK_REQ`, `PK`, `ADJUSTED`, `ADJUST_REASON` (+ `EVENT_ID` if present).

3) **Issues (table)** *(optional; only if there are rows)*  
Includes `WARNINGS` (e.g., `SEGMENT_SPLIT`) and `CRITICALS` (see [6.1](#61-issues-and-traceability)).

---

# 7. Miscellaneous

## 7.1. Curve Detection and Curvature Centers

Detects **local curvature segments** in a line layer, estimates their **radius of curvature**, and optionally computes **curvature centers** (circumcenters) and clusters them by proximity to produce a layer of grouped centers.

![CURVAS](IMAGES/RADIUS.png)

> NOTE: the “curves” layer does not contain aggregated/continuous curve stretches; each feature is a local detection based on a consecutive triplet `p1–p2–p3` (detections can overlap).

### Inputs
- **Input line layer**: vector line layer to analyze.

### Parameters
- **Densification interval** *(Distance; CRS units)*  
  Distance between inserted vertices when densifying the geometry. *(Default: 15.0)*
- **Minimum radius** *(Distance; CRS units)*: discards detections with too small radius (very tight curves or geometry noise). *(Default: 2.0)*
- **Maximum radius** *(Distance; CRS units)*: filters detections with too large radius (very flat curves approaching straight lines). *(Default: 50.0)*
- **Minimum distance between vertices** *(Distance; CRS units)*: avoids unstable calculations by ignoring triplets where `dist(p1,p2)` or `dist(p2,p3)` is too small. *(Default: 0.5)*
- **Generate curvature-centers layer** *(bool)*: if enabled, computes curvature centers and generates the clustered centroid layer. *(Default: False)*
- **Center clustering distance** *(Distance; CRS units)*: maximum distance to cluster centers into the same group. *(Default: 10.0)*  
  > Applies only if “Generate curvature-centers layer” is enabled.

**Validations (if they fail, the algorithm does not run):**
- Densification interval > 0
- Minimum radius < Maximum radius
- Distances (min vertex distance / clustering) ≥ 0

> See [Limitations and recommendations](#limitations-and-recommendations)

### Outputs

1) **Curve segment layer** (lines)  
Each feature represents a local detection (triplet `p1–p2–p3`), with attributes:
- `ID_Curva` (int): incremental identifier (0..n-1).
- `ID_Centroide` (int): associated cluster id, or `-1` if not applicable / centers not generated.
- `Radio` (double): estimated radius (in CRS units).
- `Longitud` (double): length of polyline `p1–p2–p3` (in CRS units).

2) **Centroids layer (clustered)** *(optional, points)*  
Generated only if “Generate curvature-centers layer” is enabled.
Each feature represents a cluster of centers:
- `ID_Centroide` (int): cluster identifier.
- `Radio_medio` (double): mean radius of curves assigned to the cluster.
- `Conteo` (int): number of curves in the cluster.

![CURVAS](IMAGES/EX_CURVAS.png)

### Methodology (technical summary)

1) **Densification**  
Each geometry is densified using the specified interval to obtain more regular vertices.

2) **Radius estimation from triplets**  
For each consecutive triplet `(p1, p2, p3)`:
- Compute distances `a=dist(p1,p2)`, `b=dist(p2,p3)`, `c=dist(p3,p1)`.
- Compute semiperimeter `s=(a+b+c)/2`.
- Compute area with Heron’s formula: `area = sqrt(s(s−a)(s−b)(s−c))`.
- Estimate circumcircle radius: `R = (a·b·c)/(4·area)`.
- If points are nearly colinear (area ~ 0), skip the triplet.
- Accept detection only if **`Minimum radius < R < Maximum radius`** (strict filters).

3) **Curvature centers and clustering (optional)**  
- Compute the circumcenter for each valid triplet.
- Cluster centers by proximity using “Center clustering distance”.
- Update cluster centroid as the mean of assigned centers.

### Limitations and recommendations

- **Use a projected CRS in meters** (UTM or similar).  
  The algorithm uses planar CRS distances; in geographic CRS (lat/long) values will be wrong.
- **Densification interval** is the most sensitive parameter.
  - too large → you may miss curves (too few vertices)
  - too small → higher cost and may generate too many detections (and “noise”)
- If the geometry has **noise** or too many vertices, false detections may occur.
- If the centroid layer is empty:
  - review `Minimum/maximum radius`, `Minimum distance between vertices`, and `Center clustering distance`
- **To reduce noise / excessive detections**:
  - slightly increase **Densification interval** (less sensitivity),
  - increase **Minimum radius** (discard very tight/noisy curves),
  - decrease **Maximum radius** (discard near-straight segments),
  - increase **Minimum distance between vertices** (avoid degenerate triplets).

- **To avoid missing curves**:
  - reduce **Densification interval** (more detail),
  - ensure `Minimum radius` is not too high,
  - ensure `Maximum radius` is not too low.

---

# 8. Profile & Slope

This subgroup includes two algorithms to **compute longitudinal profiles and slopes (%)** from a **DEM** (local raster or WCS) and then automatically **plot** results.

![PROFILE](IMAGES/PROFILE.PNG)

---

## 8.1. Issues and traceability

Traceability in this subgroup relies on:
- **Quality fields in outputs** (`SLOPE_TYPE` in both the table and the segmented layer).
- **Warnings in the Processing log** (e.g., missing optional fields) and **errors** when results cannot be produced (e.g., no valid data to plot).

#### `SLOPE_TYPE` (slope quality/origin)
In both the **table** and the **segmented layer**, `SLOPE_TYPE` indicates the origin/quality of the slope value:
- `REAL`: slope computed with valid elevation data at both segment ends.
- `INTERP`: slope filled by **interpolation** between neighboring segments with real slope.
- `EXTRAP`: slope filled by **extrapolation** (keeps nearest valid value).
- `NODATA`: insufficient information (slope not available).

> Note: the algorithm fills missing slopes to keep a **continuous segmented layer**, while preserving traceability with `SLOPE_TYPE`.

---

## 8.2. Profile Slope Plotter

Generates **PNG** plots and an **HTML report (index)** from a table/dataset containing:
- distance from origin (**meters**),
- elevation (profile),
- and optionally slope (%).

It is designed to plot the table generated by **Slope and Longitudinal Profile**, but it can be used with any compatible table.

![PROFILE_REPORT](IMAGES/PROFILE_REPORT.png)

### Inputs
- **Input table/layer (can be non-spatial)**.

### Parameters
- **ID field (one plot per ID)** *(optional; default `ID_Segmento`)*: if present, generates a set of plots per ID. If the field does not exist, a warning is logged and everything is treated as one group (`ALL`).
- **X field (distance from origin, meters)** *(default `Dist_Origen_metros`)*
- **Y field (elevation/profile)** *(default `Cota_SUAV`)*
- **Slope field (%)** *(optional; default `SLOPE`)*: if missing, a warning is logged and slope is treated as `NaN`.
- **Use chainage (PK) (if exists)** *(bool)*: switches X-axis **labeling** to chainage (PK) values (`K+MMM`) using the chainage (PK) field. If enabled but the field does not exist, a warning is logged and Chainage (PK) mode is disabled.
- **Chainage (PK) field (km, e.g., 91.740)** *(optional; default `m_field_PK_KM`)*
- **Show axis labels** (*bool*): Enabled: displays axis titles and labels. Disabled: no titles/labels are shown, which makes the plot easier to reuse in other languages.

### How the X axis works (distance vs chainage (PK))
- The profile is **always plotted against real distance** (`X_FIELD`, meters).
- If **Use chainage (PK)** is enabled, chainage (PK) is used **only to build the tick labels** on the X axis.

### How the Y axis works (elevation)
- The elevation axis does not necessarily start at 0; it automatically adjusts its limits to the displayed range.

### Outputs
Check `Processing Toolbox` → `Results Viewer`
- **Folder** with PNGs per ID:
  - `perfil_<ID>.png`
  - `perfil_pendiente_<ID>.png`
- **HTML report (index)** with previews and links.

![PROFILE](IMAGES/EX_PROFILE.png)

---

## 8.3. Slope and Longitudinal Profile

From a line (axis) and a DEM, generates:
- a **longitudinal profile table** (cumulative distance vs elevation) + slopes (%),
- and a **segmented layer** (micro-segments) for easier cartographic representation.

### Inputs
- **Input line layer** (axis/axes to analyze).
- **DEM (local raster or WCS)**.

### Parameters (basic)
- **Segment ID field (optional)**: if provided, preserved as `ID_Segmento` in outputs.
- **Sampling step (m). 0 = raster resolution** *(default 0)*: if `=0`, the algorithm computes an automatic step from the DEM **pixel size** (converted to meters).
- **Reverse profile direction**: swaps origin (start ↔ end).

### Advanced options
- **DEM sampling method**:
  - `Nearest`: uses the nearest pixel value. Fast, preserves original values but can look stepped.
  - `Bilinear`: interpolates from 4 neighboring pixels, producing smooth/stable results.
  - `Cubic` *(default `Cubic` with fallback to bilinear if it fails)*: larger neighborhood interpolation (smoother, slower).

- **Profile smoothing** *(default `Savitzky–Golay`)*:
  - `None`
  - `Moving average`: replaces each value with the window mean. Smooths noise but can be affected by outliers (peaks/artifacts). With the same window size, usually smooths more than Savitzky–Golay and can “flatten” changes.
  - `Moving median`: replaces each value with the window median. Robust to outliers; often a good option when the DEM has local artifacts (trees, bridges...).
  - `Savitzky–Golay` *(requires numpy; falls back to moving average if unavailable)*: smooths while better preserving shape than moving average, but can introduce noise if the window is small and polynomial order high.

- **Smoothing window**: number of samples in the filter window. Small windows smooth little; large windows smooth more but may flatten real changes. Typically 7–15.
- **Polynomial order (Savitzky–Golay)**: higher values preserve complex shapes better but can amplify noise if the window is small.

- **Use M (if present) to add chainage (PK) values to the table** *(default False)*  
  Adds `m_field_PK_KM` and `m_field_PK_MMM` to the table if the geometry has valid M.
- **M units**: `meters` / `kilometers` (to interpret M and convert to km).

### Outputs

1) **Profile table (non-spatial)** — 1 row per sample  
Fields:
- `ID_Segmento` (string)
- `Dist_Origen_metros` (double)
- `Cota_RAW_metros` (double)
- `Cota_SUAV` (double)
- `SLOPE` (double)
- `SLOPE_TYPE` (string: `REAL/INTERP/EXTRAP/NODATA`)

*(Optional, if “Use M” and M is valid on the line)*:
- `m_field_PK_KM` (double) — chainage (PK) in km (float)
- `m_field_PK_MMM` (string) — chainage (PK) formatted `km+mmm`

2) **Segmented profile (lines)** — 1 micro-segment between consecutive samples  
Fields:
- `ID_Segmento`
- Distances: `D_Ini_metros`, `D_Fin_metros`, `D_Mid_metros`
- RAW elevations: `Z_Ini_RAW_metros`, `Z_Fin_RAW_metros`, `Z_Mid_RAW_metros`
- Smoothed elevations: `Z_Ini_SUAV_metros`, `Z_Fin_SUAV_metros`, `Z_Mid_SUAV_metros`
- `SLOPE`, `SLOPE_TYPE`
- `Long_tramo` (double)

### Calculation method (technical summary)

- **DEM sampling**: samples elevation along the line for a list of distances (always includes the end).
- **Smoothing**: applied to the elevation series (`Cota_RAW_metros → Cota_SUAV`).
- **Slope (%)**: computed as `100 * dz / dx` between consecutive samples.
  - If consecutive elevations are missing, the initial slope is `None` and can later be filled (interp/extrap) to keep continuity, recorded in `SLOPE_TYPE`.
- **Chainage (PK) values from M (optional)**:
  - Interpolates M along vertices.
  - If M is not usable (e.g., some vertex has M/NaN), chainage (PK) fields are `NULL`.

### Recommendations
- For coherent slopes, use a **projected CRS (meters)** and a DEM with suitable resolution for the expected detail.
- If the DEM is noisy:
  - try `Moving median` or `Savitzky–Golay`,
  - increase the sampling step,
  - and review `SLOPE_TYPE` to distinguish real slopes from filled values.

---

# 9. License

This project is distributed under the GNU General Public License v3.0 (GPL-3.0).  
You may use it, modify it, and share it freely under the terms of this license.

---

# 10. Author

- LinkedIn: https://www.linkedin.com/in/javierhpiris
- GitHub: https://github.com/Javisionario

```
