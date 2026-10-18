# ADR-011: InSAR validation

## Context

In this document explain the different validation doing with the annotation and the tiff file using the scrip *explore_sar_tiff.py*.


Add the path to the files:

```python
tiff_path_04_january = Path("data/raw/measurement/...")
annotation_path_04_january = Path("data/raw/annotation/...")
```

To run it:

```bash
pixi run -e dev python .\src\scrips\explore_sar_tiff.py
```

The validation/inspections are:
1. Validation of th tiff sctrucutre
2. Validation of burst structure
3. Validation of annotation-defined valid samples
4. Validation inside a burst
5. Validation across a burst boundary
6. Validation of zero-valued samples versus annotation validity
7. Comparison between January 4 and January 16

The purpose was to answer three questions:
1. Are the downloaded files structurally consistent with the expected Sentinel-1 SLC products?
2. Can we correctly identify which complex SAR samples are valid according to the annotation metadata?
3. Are the two acquisitions suitable candidates for the next processing stage, coregistration?


## 1. Validation of the TIFF structure

**Funtion**: inspect_metadata_tiff()

A Sentinel-1 SLC (Single Look Complex) image stores complex radar measurements. Each sample can be represented as:
$$
S=Ae^{i\phi}=I+iQ
$$

where $A$ is amplitude, $\phi$ is phase, and $I,Q$ are the real and imaginary components.
We inspected the TIFF metadata with Rasterio.

| Property |	January 4 |	January 16 |
| --- | --- | --- |
| Driver |	GTiff |	GTiff |
| Width |	25,286 |	25,286 |
| Height |	13,644 |	13,644 |
| Bands |	1 |	1 |
| Data  type |	complex_int16 |	complex_int16 |
| CRS |	None |	None |
| Transform |	Identity |	Identity |

### Why did we check these?
**Dimensions**: They tell us how many azimuth lines and range samples are stored. They also allow us to verify consistency with the annotation XML.

**Complex data type**: InSAR requires complex samples because phase differences are fundamental to interferometry. An amplitude-only raster would not be sufficient.

**CRS and transform**: Both TIFFs have no geographic CRS and an identity transform. This is expected for these radar-coordinate measurement images; their physical geometry is described through SAR metadata rather than a conventional map projection.

**Result**: Both TIFFs have compatible structural properties.
**Limitation**: Equal dimensions do not prove that their pixels correspond to the same geographic locations.

## 2. Validation of burst structure

**Function**: inspect_annotation()

Sentinel-1 IW SLC products use TOPS (Terrain Observation with Progressive Scans) acquisition mode, in which the instrument records data in bursts.

![alt text](tops.png)

The annotation XML provides the burst organization.

|Property |	January 4 |	January 16|
| --- | --- | --- |
|Number of bursts |	9 |	9|
|Lines per burst |	1,516 |	1,516|
|Samples per burst |	25,286 |	25,286|

We verified:
$$
9\times1516=13644
$$

This matches the TIFF height exactly.

### Why is this important?

**Function**: 
The TIFF stores bursts sequentially along the azimuth dimension. We need to know where each burst begins and ends to interpret its samples correctly.

For example, using zero-based indexing:

| Burst index |	Global TIFF rows |
| --- | --- |
| 2 |	3032–4547 |
| 3 |	4548–6063 |
| 4 |	6064–7579 |

Our selected bursts 218, 219, and 220 correspond to indices 2, 3, and 4 in the burst mapping established during exploration.
Burst layout around the selected boundary

![alt text](boundary_bursts.png)

**Result**: The TIFF height and XML burst structure are consistent for both acquisitions.

**Limitation**: We have not yet fully compared burst timing, geolocation grids, and orbit geometry between acquisitions.

## 3. Validation of annotation-defined valid samples

This was the most important part of our investigation.

The annotation XML provides two arrays for each burst:
- firstValidSample
- lastValidSample

These specify the valid range samples for each azimuth line. A value of -1 indicates a line without valid samples.

We constructed a Boolean mask:
$$
M(r,c)=
\begin{cases}
1 & \text{if sample }(r,c)\text{ is annotation-valid}\\
0 & \text{otherwise}
\end{cases}
$$

For example:
```python
valid = (
    (first[:, None] >= 0)
    & (last[:, None] >= first[:, None])
    & (columns >= first[:, None])
    & (columns <= last[:, None])
)
```

## Why is this necessary?

The TIFF can contain stored complex values in regions that the product annotation identifies as invalid.

Without a mask, later algorithms could use samples that should be excluded from scientific processing.

## What did we observe?

Both acquisitions have approximately 90% annotation-valid pixels per burst.

For the two bursts around our investigated boundary:
|Burst |	January 4 valid pixels |	January 16 valid pixels |
| --- | ---- | --- |
|Index 3 |	34,608,464 |	34,608,464 |
|Index 4 |	34,584,792 |	34,584,792 |

Each burst contains:
$$
1516\times25286=38,333,576\text{ pixels}
$$

These statistics establish that the masks contain substantial valid coverage and are not simply empty arrays.

**Result**: We successfully extracted and applied the annotation validity information.

**Limitation**: Matching total valid-pixel counts does not establish identical spatial validity patterns.

## 4. Validation inside a burst

**Funtion**: compare_mask_with_tiff()

We selected a small window entirely within burst index 3:

```python
row_off = 5048
col_off = 12000
height = 256
width = 1024
```

The window contains:
$$
256\times1024=262144\text{ pixels}
$$

We compared the annotation mask with the complex TIFF values.

Each sample was classified into one of four categories.

| Classification |	January 4 |	January 16 |
|--- | --- | ---
| Valid + nonzero |	262,063 |	262,067 |
| Valid + zero |	81 |	77 |
| Invalid + nonzero |	0 |	0 |
| Invalid + zero |	0 |	0 |

### What does this demonstrate?

All pixels in this central window are annotation-valid.

A small number of those pixels have complex value zero. Therefore, a zero-valued sample is not automatically annotation-invalid either.

The four categories are exhaustive:
$$
N_{VN}+N_{VZ}+N_{IN}+N_{IZ}=N_{\text{total}}
$$

We checked this invariant in the code.

**Result**: The selected central window is fully annotation-valid, and the mask and TIFF windows have matching shapes.

## 5. Validation across a burst boundary

Next, we examined a window crossing the boundary between burst indices 3 and 4.
```python
row_off = 5950
col_off = 12000
height = 256
width = 1024
```

This covers global rows 5950–6205, crossing the boundary at row 6064.

The key complication was that the TIFF uses global row coordinates, while each burst's annotation mask uses burst-local row coordinates.

We used:
$$
b=\left\lfloor\frac{r}{1516}\right\rfloor
$$

$$
r_{\text{local}}=r\bmod1516
$$

where $r$ is the global TIFF row and $b$ is the zero-based burst index.

For example:
- Global row 6063 maps to burst index 3, local row 1515.
- Global row 6064 maps to burst index 4, local row 0.

## 6. Validation of zero-valued samples versus annotation validity

This experiment produced our most important scientific finding.

**Boundary-window results**

| Classification |	January 4 |	January 16 |
| --- | --- | --- |
| Valid + nonzero |	204,766 |	206,807 |
| Valid + zero |	34 |	41 |
| Invalid + nonzero |	54,741 |	52,961 |
| Invalid + zero |	2,603 |	2,335 |
| Total |	262,144 |	262,144 |

Nonzero values among annotation-invalid pixels

Percentages calculated within the invalid portion of each boundary window.

![alt text](invalid_portion_boundary.png)

Approximately 95.5% of annotation-invalid samples have nonzero stored values in both boundary windows.

This means:
$$
S(r,c)\neq0 \;\not\Rightarrow\; M(r,c)=1
$$

*In words*: a nonzero complex SLC sample is not necessarily scientifically valid.

The reverse implication also fails in our central-window observations: some annotation-valid pixels contain zero values.

### Why does this matter for InSAR?
If we use slc != 0 as our validity mask, we would incorrectly include thousands of invalid samples near burst boundaries.

That could affect later calculations such as coherence estimation and interferometric phase analysis.

*Result*: We have direct evidence from both acquisitions that the annotation mask and the numerical zero mask represent different properties.

## 7. Comparison between January 4 and January 16

We also compared the validity statistics for the same radar-coordinate window.

| Metric |	January 4 |	January 16 |
| --- | --- | ---
| Valid pixels |	204,800 |	206,848 |
| Invalid pixels |	57,344 |	55,296 |
| Valid fraction |	78.13% |	78.91% |

The two acquisitions have similar validity coverage but not identical counts.

This is useful evidence that both datasets have comparable structure and that the masks are acquisition-specific.

### However, we must distinguish two levels of comparison:

*Structural comparison*: We can compare dimensions, burst counts, metadata, and validity statistics before coregistration.

*Physical pixel comparison*: We cannot assume pixel (r,c) in the reference and secondary images corresponds to the same ground scatterer before coregistration.

We have completed the first type, not the second.

## 8. Acquisition Metadata Comparison

**Funtion**: compare_annotation_metadata()

The objective of this validation is to verify that the reference (January 4, 2025) and secondary (January 16, 2025) Sentinel-1 SLC acquisitions have compatible structural characteristics before proceeding to coregistration.

| Property | Reference — Jan 4 | Secondary — Jan 16 | Result |
|---|---|---|---|
| Mission | S1A | S1A | Match |
| Swath | IW3 | IW3 | Match |
| Polarization | VV | VV | Match |
| Lines per burst | 1,516 | 1,516 | Match |
| Samples per burst | 25,286 | 25,286 | Match |
| Burst count | 9 | 9 | Match |
| Start time | 2025-01-04 18:10:41.991507 | 2025-01-16 18:10:40.993084 | Expected difference |
| Stop time | 2025-01-04 18:11:07.176182 | 2025-01-16 18:11:06.169538 | Expected difference |
| Burst azimuth timestamps | 9 timestamps | 9 timestamps | Same count; similar timing patterns |
| Temporal baseline | — | 11 days, 23:59:59.001577 | Approximately 12 days |

**Validation result: PASSED — Structural compatibility**

All six structural metadata properties match. Both acquisitions were collected by Sentinel-1A using the IW3 subswath and VV polarization, and both contain nine bursts with identical raster dimensions.

### Temporal Baseline Validation

The temporal baseline is the elapsed time between the reference and secondary acquisitions:

$$
\Delta t = t_{\mathrm{secondary}} - t_{\mathrm{reference}}
$$

The calculated temporal baseline is:

**11 days, 23:59:59.001577 (approximately 12 days).**

This result is consistent with the expected repeat-pass acquisition interval for the selected Sentinel-1 pair.

The acquisition timestamps are expected to differ because the observations were collected on separate dates. Identical acquisition timestamps are not required for interferometric compatibility.

### Burst Timing Analysis

Both annotations contain nine burst azimuth timestamps.

The intervals between successive burst start times are approximately 2.75–2.77 seconds for both acquisitions, indicating similar burst sequencing.

The secondary acquisition starts approximately one second earlier in time of day than the reference acquisition.

These observations support preliminary timing compatibility, but they do not establish exact spatial correspondence between bursts.

## 9. Geolocation Grid and Selected-Burst Geographic Coverage Validation

**Function**: compare_geolocation_summary()

The objective of this validation is to compare the geographic coverage of the Sentinel-1 SLC reference acquisition (January 4, 2025) and secondary acquisition (January 16, 2025).

This validation is performed before coregistration to determine whether both acquisitions observe approximately the same geographic region and whether the selected burst regions are suitable candidates for interferometric processing.

Sentinel-1 SLC measurements are stored in **radar coordinates**, defined by azimuth (along-track position) and slant range (distance from the satellite to the target).

The annotation XML contains a `geolocationGrid`, which associates sampled radar-image positions (`line`, `pixel`) with geographic coordinates (`latitude`, `longitude`).

These geographic coordinates support preliminary coverage assessment but do not establish precise pixel-level alignment.

### Full-Image Geolocation Grid Extraction

The geolocation points were extracted from the following XML structure:

```text
geolocationGrid
└── geolocationGridPointList
    └── geolocationGridPoint
        ├── line
        ├── pixel
        ├── latitude
        └── longitude
```

Both acquisitions contain **210 geolocation grid points**.

| Property | Reference — Jan 4 | Secondary — Jan 16 |
|---|---:|---:|
| Geolocation points | 210 | 210 |
| Minimum latitude | 36.969332° | 36.969582° |
| Maximum latitude | 38.607725° | 38.607474° |
| Minimum longitude | −2.714781° | −2.714407° |
| Maximum longitude | −1.454277° | −1.454042° |

**Result: PASSED — Preliminary full-image geographic bounding-box overlap.**

The reference and secondary geographic bounds are very similar, indicating extensive overlap between their sampled geolocation extents.

The differences between corresponding geographic boundaries are small and consistent with slightly different acquisition geometries.

The project's approximate area of interest (AOI) is:

| Coordinate | Minimum | Maximum |
|---|---:|---:|
| Latitude | 37.55° | 37.75° |
| Longitude | −1.80° | −1.60° |

The AOI lies within both full-image geographic bounding boxes.

**Limitation:** Bounding-box containment does not prove that the entire AOI is covered by valid SAR measurements. The actual acquisition footprint may be nonrectangular, and some samples are invalid according to the annotation validity masks.

### Geolocation Grid Sampling Structure

The extracted geolocation grid contains the following unique azimuth lines:

```text
[0, 1516, 3032, 4548, 6064,
 7580, 9096, 10612, 12128, 13643]
```

There are **10 unique azimuth lines**, each containing **21 range positions**:

$$
10 \times 21 = 210 \text{ geolocation points}
$$

The range sample positions are:

```text
[0, 1265, 2530, 3795, 5060, 6325, 7590,
 8855, 10120, 11385, 12650, 13915, 15180,
 16445, 17710, 18975, 20240, 21505,
 22770, 24035, 25285]
```

The azimuth sampling lines correspond to the boundaries between the nine bursts, together with the final image row.

Each burst contains 1,516 azimuth lines, and the measurement TIFF has 13,644 rows:

$$
9 \times 1516 = 13644
$$

This confirms that the geolocation sampling pattern is consistent with the previously validated burst structure.

**Important distinction:** The geolocation grid is sparse. Its 210 points do not represent all measurement pixels, and its sampled geographic coordinates must not be interpreted as a complete map of valid SAR observations.

### Selected-Burst Geolocation Extraction

The processing workflow focuses on three selected burst regions, using the previously established mapping:

| Selected burst ID | Zero-based index | Measurement TIFF rows | Geolocation boundary lines |
|---|---:|---|---|
| 218 | 2 | 3032–4547 | 3032, 4548 |
| 219 | 3 | 4548–6063 | 4548, 6064 |
| 220 | 4 | 6064–7579 | 6064, 7580 |

For geographic extent estimation, both boundary lines were included.

Each selected burst region therefore has:

$$
2 \text{ boundary lines} \times 21 \text{ range samples} = 42 \text{ points}
$$

This is different from reading SLC measurement data, where burst row ranges remain half-open and the next burst's first row must not be included in the preceding burst.

The boundary-line samples are used here only to approximate geographic extent.

### Selected-Burst Geographic Coverage — Reference Acquisition

| Burst | Points | Latitude range | Longitude range |
|---|---:|---|---|
| 218 | 42 | 37.302407° to 37.591824° | −2.489724° to −1.519523° |
| 219 | 42 | 37.467897° to 37.757572° | −2.528022° to −1.549744° |
| 220 | 42 | 37.633290° to 37.923510° | −2.564660° to −1.582442° |

### Selected-Burst Geographic Coverage — Secondary Acquisition

| Burst | Points | Latitude range | Longitude range |
|---|---:|---|---|
| 218 | 42 | 37.302287° to 37.591694° | −2.489394° to −1.519208° |
| 219 | 42 | 37.467777° to 37.757566° | −2.527716° to −1.549426° |
| 220 | 42 | 37.633295° to 37.923381° | −2.564324° to −1.582147° |

**Result: PASSED — Preliminary selected-burst geographic bounding-box overlap.**

For each selected burst region, the reference and secondary bounding boxes are closely matched.

This supports the interpretation that corresponding burst regions observe approximately the same geographic areas.

However, matching geographic bounds do not establish exact burst identity or coregistration accuracy.

### Selected-Burst Coverage Relative to the AOI

The AOI extends from approximately 37.55° to 37.75° latitude and −1.80° to −1.60° longitude.

Based on the extracted geographic bounding boxes:

| Burst | Approximate AOI relationship | Interpretation |
|---|---|---|
| 218 | Intersects the southern AOI | Partial bounding-box overlap |
| 219 | Encompasses the AOI latitude interval | Broad bounding-box overlap |
| 220 | Intersects the northern AOI | Partial bounding-box overlap |

All three selected burst bounding boxes intersect the AOI in both latitude and longitude.

The same general coverage pattern is observed in the reference and secondary acquisitions.

**Scientific limitation:** Overlap in latitude and longitude bounds is a necessary preliminary check, but it is not sufficient to establish actual footprint intersection or valid-pixel coverage. A more rigorous validation would construct approximate geographic footprint polygons from the boundary samples and evaluate their intersection with the AOI, accounting for the measurement validity masks where appropriate.



## 10. Orbit Compatibility and Radar Acquisition Geometry Validation

**Function**: compare_orbit_metadata()

### Objective

Validate that the selected Sentinel-1 SLC reference and secondary acquisitions have compatible orbital characteristics and radar sampling parameters before proceeding to coregistration and interferogram generation.

**Acquisition pair:**

- **Reference:** 2025-01-04
- **Secondary:** 2025-01-16
- **Mission:** Sentinel-1A
- **Swath:** IW3
- **Polarization:** VV
- **Temporal baseline:** Approximately 12 days

### Orbit Compatibility Results

The orbit metadata comparison produced the following results:

| Parameter | Reference | Secondary | Validation |
|---|---|---|---|
| Relative orbit | 1 | 1 | PASS |
| Orbit direction | ASCENDING | ASCENDING | PASS |
| Absolute orbit | 57298 | 57473 | PASS — distinct acquisitions |
| Absolute orbit difference | — | 175 orbits | Consistent with a 12-day repeat cycle |

**Result: PASS — Orbit metadata compatibility.**

Both acquisitions belong to relative orbit 1 and were collected during ascending passes. Their absolute orbit numbers differ by 175, consistent with Sentinel-1's nominal 12-day orbital repeat cycle.

Matching relative orbit and pass direction support the selection of a repeat-pass interferometric pair. However, these checks alone do not establish precise orbital alignment or pixel-level correspondence.

### Radar Acquisition Geometry Results

Radar parameters were extracted from the Sentinel-1 annotation XML files and compared.

| Parameter | Reference | Secondary | Difference | Validation |
|---|---:|---:|---:|---|
| Radar frequency (Hz) | 5405000454.33 | 5405000454.33 | 0 | PASS |
| Range sampling rate (Hz) | 64345238.1257 | 64345238.1257 | 0 | PASS |
| Azimuth time interval (s) | 0.0020555563 | 0.0020555563 | 0 | PASS |
| Range pixel spacing (m) | 2.329562 | 2.329562 | 0 | PASS |
| Azimuth pixel spacing (m) | 13.90341 | 13.90341 | 0 | PASS |
| Slant-range time (s) | 0.00601709906465 | 0.00601709906465 | 0 | Reported |
| Mid-swath incidence angle (°) | 43.9031460883 | 43.903906926 | +0.0007608378 | Reported |

**Result: PASS — Radar sampling parameter consistency.**

All five parameters selected for numerical compatibility checking match exactly at the reported precision.

The slant-range time also matches. The mid-swath incidence angles differ by approximately:

$$
\Delta\theta = 43.903906926 - 43.9031460883
$$

$$
\boxed{\Delta\theta \approx 0.000761^\circ}
$$

This small difference is consistent with similar viewing geometries. It does not demonstrate precise geometric alignment.


## 11. Sentinel-1 TOPS Burst Timing Comparison

### Objective

Evaluate the temporal consistency of corresponding candidate bursts between the selected Sentinel-1 SLC reference and secondary acquisitions.

This validation supports the assessment of repeat-pass acquisition compatibility before coregistration.

**Acquisition pair:**

- **Reference:** 2025-01-04
- **Secondary:** 2025-01-16
- **Mission:** Sentinel-1A
- **Swath:** IW3
- **Polarization:** VV
- **Relative orbit:** 1
- **Orbit direction:** ASCENDING
- **Selected candidate burst IDs:** 218, 219, 220

### Methodology

Burst azimuth acquisition times were extracted from each annotation XML file using:

`swathTiming/burstList/burst/azimuthTime`

The comparison used the following candidate mapping:

| Candidate burst ID | Zero-based XML index |
|---|---:|
| 218 | 2 |
| 219 | 3 |
| 220 | 4 |

The mapping is provisional and has not yet been independently verified against authoritative burst catalog identifiers.

For each selected burst, the inter-acquisition timing offset was calculated:

$$
\Delta t_i = t_{\text{secondary},i} - t_{\text{reference},i}
$$

The first burst in each annotation file (index 0) was used as a timing reference:

$$
\Delta t_0 = t_{\text{secondary},0} - t_{\text{reference},0}
$$

The relative timing residual was then calculated:

$$
r_i = \Delta t_i - \Delta t_0
$$

This residual measures the difference between the inter-acquisition timing offset of burst $i$ and that of burst index 0.

It does not directly measure geometric coregistration error.

### Observed Results

| Candidate burst ID | XML index | Reference azimuth time | Secondary azimuth time | Timing residual |
|---|---:|---|---|---:|
| 218 | 2 | 2025-01-04 18:10:47.512731 | 2025-01-16 18:10:46.508141 | −6.166 ms |
| 219 | 3 | 2025-01-04 18:10:50.271288 | 2025-01-16 18:10:49.266697 | −6.167 ms |
| 220 | 4 | 2025-01-04 18:10:53.027789 | 2025-01-16 18:10:52.025254 | −4.111 ms |

**Execution result: Candidate burst timing comparison — COMPLETED.**

The method successfully extracted burst timestamps and calculated relative timing residuals for all three selected candidate bursts.

### Scientific Interpretation

The timing residuals for candidate bursts 218 and 219 are nearly identical:

- Burst 218: −6.166 ms
- Burst 219: −6.167 ms

Candidate burst 220 has a residual of −4.111 ms.

The difference between the maximum and minimum residuals is approximately:

$$
\Delta r = -4.111 - (-6.167)
$$

$$
\boxed{\Delta r \approx 2.056\text{ ms}}
$$

The previously extracted azimuth time interval is:

$$
\Delta t_{\text{azimuth}} = 0.0020555563\text{ s}
$$

$$
\boxed{\Delta t_{\text{azimuth}} \approx 2.056\text{ ms}}
$$

Therefore, the observed residual spread is approximately equivalent to one azimuth sampling interval.

This observation is consistent with a sample-scale difference in relative burst timing. However, the available evidence does not establish its physical cause.

The timing comparison alone cannot determine whether corresponding radar pixels observe identical ground targets.


## 12. Sentinel-1 TOPS Doppler Centroid, Azimuth FM-Rate, and Antenna Steering Validation

### Objective

Extract, evaluate, and compare the TOPS Doppler centroid, azimuth frequency modulation (FM) rate, and antenna steering metadata for the Sentinel-1A reference and secondary acquisitions.

This validation provides an initial assessment of Doppler-related acquisition geometry before coregistration.

**Acquisition pair:**

- **Reference:** 2025-01-04
- **Secondary:** 2025-01-16
- **Mission:** Sentinel-1A
- **Swath:** IW3
- **Polarization:** VV
- **Selected burst IDs:** 218, 219, 220
- **Zero-based annotation indices:** 2, 3, 4

### Implementation

**Function:** `validate_tops_doppler_geometry()`

**Supporting functions:**

- `_extract_tops_geometry()`
- `_evaluate_polynomial()`
- `_nearest_tops_record()`

The implementation extracts the following information from both annotation XML files:

1. Antenna azimuth steering rate.
2. Doppler centroid polynomial coefficients and their reference times.
3. Azimuth FM-rate polynomial coefficients and their reference times.
4. Doppler centroid RMS estimation errors and quality flags.
5. Radar sampling parameters and burst azimuth timestamps.

The polynomial models are evaluated over 256 sampled range positions across the recorded swath.

For each selected burst, the implementation chooses the Doppler centroid and FM-rate records with azimuth timestamps closest to the burst midpoint.

### Doppler Centroid and FM-Rate Polynomial Evaluation

The Doppler centroid represents the center frequency of the azimuth Doppler spectrum.

The azimuth FM-rate describes the rate of change of Doppler frequency with azimuth time for a radar target.

Both parameters vary with slant range and are represented by polynomial models in the annotation metadata.

For a polynomial with coefficients \(c_0,c_1,c_2\), the evaluation is:

$$
P(\tau)=c_0+c_1(\tau-t_0)+c_2(\tau-t_0)^2
$$

where:

- \(\tau\) is the two-way slant-range time in seconds.
- \(t_0\) is the polynomial reference slant-range time.
- \(c_0,c_1,c_2\) are the annotation polynomial coefficients.

The slant-range time associated with a range sample \(p\) is calculated as:

$$
\tau(p)=\tau_{\mathrm{first}}+\frac{p}{f_s}
$$

where \(f_s\) is the range sampling frequency.

The Doppler centroid polynomial produces values in Hz, while the azimuth FM-rate polynomial produces values in Hz/s.

### Antenna Steering Rate Results

| Parameter | Reference — Jan 4 | Secondary — Jan 16 |
|---|---:|---:|
| Azimuth steering rate (°/s) | 1.397440818 | 1.397440818 |
| Doppler centroid records | 11 | 11 |
| Azimuth FM-rate records | 11 | 11 |

**Result: PASS — Antenna steering rate compatibility.**

The reported antenna steering rates are identical for both acquisitions.

The steering rate describes the angular motion of the antenna beam during TOPS acquisition. It must not be confused with the azimuth FM-rate, which has different units and physical meaning.

### Doppler Centroid Results

The following values were obtained by evaluating the selected Doppler centroid polynomials across the sampled range positions.

| Burst ID | Reference Doppler centroid (Hz) | Secondary Doppler centroid (Hz) |
|---|---:|---:|
| 218 | 21.846 to 30.624 | 20.149 to 29.871 |
| 219 | 24.298 to 30.303 | 22.103 to 28.252 |
| 220 | 22.468 to 31.283 | 17.974 to 28.353 |

The evaluated Doppler centroid values are finite and vary with slant range.

Differences between the reference and secondary acquisitions are expected because the Doppler centroid estimates are acquisition-specific.

These differences do not, by themselves, indicate incompatibility.

### Azimuth FM-Rate Results

| Burst ID | Reference FM-rate (Hz/s) | Secondary FM-rate (Hz/s) |
|---|---:|---:|
| 218 | −2055.981 to −1925.552 | −2055.982 to −1925.553 |
| 219 | −2055.897 to −1925.465 | −2055.898 to −1925.466 |
| 220 | −2055.834 to −1925.398 | −2055.834 to −1925.398 |

The evaluated azimuth FM-rates are negative for all selected bursts in both acquisitions.

The values vary across slant range and are nearly identical between the reference and secondary acquisitions at the reported precision.

**Result: PASS — Numerical FM-rate consistency.**

This confirms that the extracted polynomial models produce finite, nonzero, and closely matching FM-rate values for the selected bursts.

It does not independently establish that the selected polynomial records are the correct models for every azimuth position within each burst.

### Doppler Centroid RMS Error Results

The annotation metadata provides an RMS error associated with each Doppler centroid estimate.

| Burst ID | Reference RMS error (Hz) | Secondary RMS error (Hz) |
|---|---:|---:|
| 218 | 6.804 | 6.296 |
| 219 | 7.187 | 7.250 |
| 220 | 5.004 | 6.696 |

The RMS errors are of similar magnitude between acquisitions.

No threshold-exceeded warnings were reported for the selected Doppler records during execution.

However, the RMS error should not be interpreted as a direct measure of coregistration accuracy or interferometric phase quality.

### Validation Checks Performed

The implementation successfully checked:

- Presence of Doppler centroid and azimuth FM-rate records.
- Extraction of antenna steering metadata.
- Finite polynomial coefficients and radar sampling parameters.
- Chronological ordering of estimate records.
- Compatibility of reference and secondary steering rates.
- Compatibility of range sampling frequencies.
- Finite evaluated Doppler centroid values.
- Finite and nonzero evaluated azimuth FM-rates.
- Doppler centroid RMS error reporting.

The execution completed with:

```text
TOPS Doppler metadata and numerical checks: PASSED
```

