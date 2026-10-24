import logging
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.windows import Window

from insar_platform.config.logger import LoggingManager


@staticmethod
def _build_burst_mask(
    first: list[int],
    last: list[int],
    samples_per_burst: int,
) -> np.ndarray:
    first_arr = np.asarray(first)
    last_arr = np.asarray(last)

    assert first_arr.shape == last_arr.shape

    columns = np.arange(samples_per_burst)[None, :]

    mask = (
        (first_arr[:, None] >= 0)
        & (last_arr[:, None] >= first_arr[:, None])
        & (columns >= first_arr[:, None])
        & (columns <= last_arr[:, None])
    )

    return mask


class ExploreSARTiff:
    def __init__(
        self,
        tiff_path: Path,
        reference_metadata: Path,
        secondary_metadata: Path,
    ):
        self.logger = logging.getLogger(__name__)

        self.tiff_path = tiff_path
        self.reference_metadata = reference_metadata
        self.secondary_metadata = secondary_metadata

    def run(self):
        self.inspect_metadata_tiff()
        mask = self.inspect_annotation()
        self.compare_mask_with_tiff(mask)
        self.inspect_boundary(mask)
        self.compare_annotation_metadata()
        self.compare_geolocation_summary()
        self.compare_orbit_metadata()
        self.compare_burst_timing()
        self.validate_tops_doppler_geometry()
        self.inspect_small_amplitude_window()

    def inspect_metadata_tiff(self):
        self.logger.info("Inspecting TIFF metadata: %s", self.tiff_path)
        with rasterio.open(self.tiff_path) as src:
            self.logger.info("Driver: %s", src.driver)
            self.logger.info("Width: %s", src.width)
            self.logger.info("Height: %s", src.height)
            self.logger.info("Bands: %s", src.count)
            self.logger.info("Data types: %s", src.dtypes)
            self.logger.info("CRS: %s", src.crs)
            self.logger.info("Transform: %s", src.transform)

    def inspect_annotation(self):

        self.logger.info("\nInspecting annotation XML: %s", self.reference_metadata)
        root = ET.parse(self.reference_metadata).getroot()

        swath_timing = root.find("swathTiming")

        if swath_timing is None:
            raise ValueError("No swathTiming element found in the annotation XML")

        lines_per_burst = swath_timing.find("linesPerBurst")

        if lines_per_burst is None:
            raise ValueError("No linesPerBurst element found in the annotation XML")

        samples_per_burst = swath_timing.find("samplesPerBurst")

        if samples_per_burst is None:
            raise ValueError("No samplesPerBurst element found in the annotation XML")

        bursts = swath_timing.findall("./burstList/burst")
        if not bursts:
            raise ValueError("No bursts found in the annotation XML")

        self.logger.info("Lines per burst: %s", lines_per_burst.text)
        self.logger.info("Samples per burst: %s", samples_per_burst.text)
        self.logger.info("Number of bursts: %s", len(bursts))

        mask = {}

        for index, burst in enumerate(bursts):
            first_text = burst.findtext("firstValidSample")
            last_text = burst.findtext("lastValidSample")

            if first_text is None or last_text is None:
                raise ValueError(f"Missing validity metadata for burst {index}")

            first = [int(v) for v in first_text.split()]
            last = [int(v) for v in last_text.split()]

            # Build a mask for the current burst based on the first and last valid samples
            mask[index] = _build_burst_mask(
                first=first,
                last=last,
                samples_per_burst=25286,
            )

            if lines_per_burst.text is None:
                raise ValueError(
                    f"Burst {index}: lines per burst is missing in the annotation XML"
                )

            # self.logger.info(
            #     "Burst index %s: firstValidSample=%s (len=%s), lastValidSample=%s (len=%s)",
            #     index,
            #     first,
            #     len(first),
            #     last,
            #     len(last),
            # )
            # self.logger.info(
            #     "Expected lines per burst: %s",
            #     lines_per_burst.text,
            # )

            if len(first) != int(lines_per_burst.text):
                raise ValueError(
                    f"Burst {index}: unexpected firstValidSample length. "
                    f"Expected {lines_per_burst.text}, got {len(first)}"
                )

            if len(last) != int(lines_per_burst.text):
                raise ValueError(
                    f"Burst {index}: unexpected lastValidSample length. "
                    f"Expected {lines_per_burst.text}, got {len(last)}"
                )

            if samples_per_burst.text is None:
                raise ValueError(
                    f"Burst {index}: samples per burst is missing in the annotation XML"
                )

            valid_lines = sum(
                0 <= f <= l < int(samples_per_burst.text) for f, l in zip(first, last)
            )

            current_mask = mask[index]
            self.logger.info(
                "Burst %d | valid lines=%d/%d | valid pixels=%d/%d (%.2f%%)",
                index,
                valid_lines,
                int(lines_per_burst.text),
                np.count_nonzero(current_mask),
                current_mask.size,
                100 * np.mean(current_mask),
            )

        return mask

    def compare_mask_with_tiff(
        self,
        mask: dict[int, np.ndarray],
    ) -> None:
        self.logger.info("\nComparing annotation mask with TIFF")

        burst_index = 3
        lines_per_burst = 1516

        row_off = burst_index * lines_per_burst + 500
        col_off = 12000
        height = 256
        width = 1024

        # This function analyzes a window within one burst.
        burst_start = burst_index * lines_per_burst
        burst_end = burst_start + lines_per_burst

        if not (burst_start <= row_off and row_off + height <= burst_end):
            raise ValueError("Window crosses a burst boundary")

        # Read the corresponding complex SLC samples.
        window = Window(
            col_off=col_off,  # type: ignore
            row_off=row_off,  # type: ignore
            width=width,  # type: ignore
            height=height,  # type: ignore
        )

        with rasterio.open(self.tiff_path) as src:
            if row_off + height > src.height or col_off + width > src.width:
                raise ValueError("Window exceeds TIFF dimensions")

            slc = src.read(1, window=window)

        # Retrieve the annotation mask for the selected burst.
        if burst_index not in mask:
            raise ValueError(f"Missing annotation mask for burst {burst_index}")

        burst_mask = mask[burst_index]

        # Convert global TIFF row indices into burst-local indices.
        local_row_off = row_off - burst_start

        mask_window = burst_mask[
            local_row_off : local_row_off + height,
            col_off : col_off + width,
        ]

        # Validate spatial alignment.
        if slc.shape != mask_window.shape:
            raise ValueError(
                f"Shape mismatch: SLC={slc.shape}, mask={mask_window.shape}"
            )

        # Classify samples by annotation validity and stored value.
        valid = mask_window
        zero = slc == 0

        counts = {
            "valid_nonzero": int(np.count_nonzero(valid & ~zero)),
            "valid_zero": int(np.count_nonzero(valid & zero)),
            "invalid_nonzero": int(np.count_nonzero(~valid & ~zero)),
            "invalid_zero": int(np.count_nonzero(~valid & zero)),
        }

        total = slc.size

        if sum(counts.values()) != total:
            raise ValueError("Pixel classification is incomplete")

        self.logger.info(
            "Burst index: %d | TIFF rows: [%d, %d) | Range columns: [%d, %d)",
            burst_index,
            row_off,
            row_off + height,
            col_off,
            col_off + width,
        )

        for category, count in counts.items():
            self.logger.info(
                "%s: %d (%.2f%%)",
                category,
                count,
                100 * count / total,
            )

        self.logger.info(
            "Observed zero fraction: %.4f",
            zero.mean(),
        )

        self.logger.info(
            "Annotation invalid fraction: %.4f",
            (~valid).mean(),
        )

    def inspect_boundary(
        self,
        mask: dict[int, np.ndarray],
    ) -> None:
        self.logger.info("Inspecting burst boundary with annotation mask")

        lines_per_burst = 1516

        row_off = 5950
        col_off = 12000
        height = 256
        width = 1024

        # The boundary between burst indices 3 and 4.
        boundary_row = 4 * lines_per_burst  # 6064

        # 1. Read the complex SLC window.
        window = Window(
            col_off=col_off,  # type: ignore
            row_off=row_off,  # type: ignore
            width=width,  # type: ignore
            height=height,  # type: ignore
        )

        with rasterio.open(self.tiff_path) as src:
            if row_off + height > src.height or col_off + width > src.width:
                raise ValueError("Boundary window exceeds TIFF dimensions")

            slc = src.read(1, window=window)

        # 2. Construct the annotation mask for the same window.
        mask_window = np.zeros(slc.shape, dtype=bool)

        for local_row in range(height):
            global_row = row_off + local_row

            burst_index = global_row // lines_per_burst
            burst_local_row = global_row % lines_per_burst

            if burst_index not in mask:
                raise ValueError(f"Missing annotation mask for burst {burst_index}")

            mask_window[local_row, :] = mask[burst_index][
                burst_local_row,
                col_off : col_off + width,
            ]

        # 3. Validate alignment after populating the mask.
        if slc.shape != mask_window.shape:
            raise ValueError(
                f"Shape mismatch: SLC={slc.shape}, mask={mask_window.shape}"
            )

        # Verify the rows immediately adjacent to the boundary.
        last_row_before = boundary_row - 1 - row_off
        first_row_after = boundary_row - row_off

        if not (0 <= last_row_before < height):
            raise ValueError("Window does not include the burst boundary")

        if not (0 <= first_row_after < height):
            raise ValueError("Window does not include the burst boundary")

        if not np.array_equal(
            mask_window[last_row_before],
            mask[3][lines_per_burst - 1, col_off : col_off + width],
        ):
            raise ValueError("Incorrect mask mapping before boundary")

        if not np.array_equal(
            mask_window[first_row_after],
            mask[4][0, col_off : col_off + width],
        ):
            raise ValueError("Incorrect mask mapping after boundary")

        # 4. Classify pixels using the populated mask.
        valid = mask_window
        zero = slc == 0

        counts = {
            "valid_nonzero": int(np.count_nonzero(valid & ~zero)),
            "valid_zero": int(np.count_nonzero(valid & zero)),
            "invalid_nonzero": int(np.count_nonzero(~valid & ~zero)),
            "invalid_zero": int(np.count_nonzero(~valid & zero)),
        }

        total = slc.size

        if sum(counts.values()) != total:
            raise ValueError("Pixel classification is incomplete")

        self.logger.info(
            "Boundary window | rows=[%d, %d) | columns=[%d, %d)",
            row_off,
            row_off + height,
            col_off,
            col_off + width,
        )

        self.logger.info(
            "Boundary mask | shape=%s | valid=%d | invalid=%d",
            mask_window.shape,
            np.count_nonzero(valid),
            np.count_nonzero(~valid),
        )

        for category, count in counts.items():
            self.logger.info(
                "%s: %d (%.2f%%)",
                category,
                count,
                100 * count / total,
            )

        # 5. Inspect selected rows.
        for local_row in (0, 50, 113, 114, 150, 255):
            self.logger.info(
                "Global row=%d | valid fraction=%.4f | zero fraction=%.4f",
                row_off + local_row,
                valid[local_row].mean(),
                zero[local_row].mean(),
            )

        # 6. Calculate per-row statistics.
        global_rows = np.arange(row_off, row_off + height)

        zero_fraction = zero.mean(axis=1)
        invalid_fraction = (~valid).mean(axis=1)

        # 7. Visualize the boundary.
        plt.figure(figsize=(10, 5))

        plt.plot(
            global_rows,
            zero_fraction,
            label="Observed zero fraction",
        )

        plt.plot(
            global_rows,
            invalid_fraction,
            label="Annotation-invalid fraction",
        )

        plt.axvline(
            boundary_row,
            color="red",
            linestyle="--",
            label="Burst boundary",
        )

        plt.xlabel("Global azimuth line")
        plt.ylabel("Fraction of pixels")
        plt.title("Sentinel-1 TOPS Burst Boundary Validation")
        plt.legend()
        plt.tight_layout()
        plt.show()

    def compare_annotation_metadata(self) -> None:

        reference_metadata = self.extract_annotation_metadata(self.reference_metadata)
        secondary_metadata = self.extract_annotation_metadata(self.secondary_metadata)

        matching_fields = (
            "mission",
            "swath",
            "polarization",
            "lines_per_burst",
            "samples_per_burst",
            "burst_count",
        )

        for field in matching_fields:
            if reference_metadata[field] != secondary_metadata[field]:
                raise ValueError(
                    f"Incompatible {field}: "
                    f"reference={reference_metadata[field]}, "
                    f"secondary={secondary_metadata[field]}"
                )

        self.logger.info("Acquisition structural compatibility: PASSED")

        reference_start_value = reference_metadata["start_time"]
        secondary_start_value = secondary_metadata["start_time"]

        if not isinstance(reference_start_value, str):
            raise TypeError("Reference start_time must be a string")

        if not isinstance(secondary_start_value, str):
            raise TypeError("Secondary start_time must be a string")

        reference_start = datetime.fromisoformat(reference_start_value)
        secondary_start = datetime.fromisoformat(secondary_start_value)

        temporal_baseline = secondary_start - reference_start

        self.logger.info("Temporal baseline: %s", temporal_baseline)

        self.logger.info("Reference metadata: %s", reference_metadata)
        self.logger.info("Secondary metadata: %s", secondary_metadata)

    def compare_geolocation_summary(self) -> None:

        points_reference = self._extract_geolocation_points(self.reference_metadata)
        points_secondary = self._extract_geolocation_points(self.secondary_metadata)

        self._summary_geolocation_points(points_reference, "Reference")
        self._summary_geolocation_points(points_secondary, "Secondary")

        burst_indices = {
            218: 2,
            219: 3,
            220: 4,
        }

        reference_summary = self._summarize_selected_bursts(
            points_reference,
            lines_per_burst=1516,
            burst_indices=burst_indices,
        )

        secondary_summary = self._summarize_selected_bursts(
            points_secondary,
            lines_per_burst=1516,
            burst_indices=burst_indices,
        )

        self.logger.info("Reference summary: %s", reference_summary)
        self.logger.info("Secondary summary: %s", secondary_summary)

        for burst_id, burst_index in burst_indices.items():
            start = burst_index * 1516
            end = start + 1516

            selected = [
                point for point in points_reference if start <= point["line"] < end
            ]

            self.logger.info(
                "Burst %s | Lines: %s | Pixels: %s",
                burst_id,
                sorted(p["line"] for p in selected),
                sorted(p["pixel"] for p in selected),
            )

        unique_lines = sorted({point["line"] for point in points_reference})

        self.logger.info(
            "Unique geolocation lines: %s",
            unique_lines,
        )

        self.logger.info(
            "Number of unique lines: %d",
            len(unique_lines),
        )

    def compare_orbit_metadata(self) -> None:
        self.logger.info("Comparing orbit metadata...")

        reference = self._extract_orbit_metadata(self.reference_metadata)
        secondary = self._extract_orbit_metadata(self.secondary_metadata)

        # 1. Validate orbit compatibility
        for field in ("relative_orbit", "orbit_direction"):
            if reference[field] != secondary[field]:
                raise ValueError(
                    f"{field} mismatch: "
                    f"reference={reference[field]}, "
                    f"secondary={secondary[field]}"
                )

        if reference["absolute_orbit"] == secondary["absolute_orbit"]:
            raise ValueError(
                "Identical absolute orbit numbers: "
                "expected distinct repeat-pass acquisitions"
            )

        self.logger.info(
            "Orbit compatibility: PASSED | "
            "Relative orbit: %s | Direction: %s | "
            "Absolute orbits: %s / %s",
            reference["relative_orbit"],
            reference["orbit_direction"],
            reference["absolute_orbit"],
            secondary["absolute_orbit"],
        )

        # 2. Extract radar acquisition geometry
        self.logger.info("Extracting reference radar geometry...")
        reference_geometry = self._extract_radar_geometry(self.reference_metadata)

        self.logger.info("Extracting secondary radar geometry...")
        secondary_geometry = self._extract_radar_geometry(self.secondary_metadata)

        # 3. Log extracted values before choosing validation thresholds
        self.logger.info(
            "Reference radar geometry: %s",
            reference_geometry,
        )
        self.logger.info(
            "Secondary radar geometry: %s",
            secondary_geometry,
        )

        # 4. Compare geometry
        self._compare_radar_geometry(
            reference_geometry,
            secondary_geometry,
        )

    def compare_burst_timing(self) -> None:
        self.logger.info("Comparing burst timing...")

        reference = self._extract_burst_timing(self.reference_metadata)

        secondary = self._extract_burst_timing(self.secondary_metadata)

        if len(reference) != len(secondary):
            raise ValueError(
                "Burst count mismatch: "
                f"reference={len(reference)}, "
                f"secondary={len(secondary)}"
            )

        selected_bursts = {
            218: 2,
            219: 3,
            220: 4,
        }

        acquisition_offset = secondary[0] - reference[0]

        for burst_id, index in selected_bursts.items():
            if index >= len(reference):
                raise ValueError(f"Burst {burst_id}: index {index} out of range")

            reference_time = reference[index]
            secondary_time = secondary[index]

            burst_offset = secondary_time - reference_time

            timing_residual = (burst_offset - acquisition_offset).total_seconds()

            self.logger.info(
                "Burst %s | Index: %s | "
                "Reference: %s | Secondary: %s | "
                "Timing residual: %.6f s",
                burst_id,
                index,
                reference_time.isoformat(),
                secondary_time.isoformat(),
                timing_residual,
            )

        self.logger.info("Candidate burst timing comparison: COMPLETED")

    def validate_tops_doppler_geometry(self) -> None:
        self.logger.info("Validating TOPS Doppler and steering geometry")

        reference = self._extract_tops_geometry(self.reference_metadata)
        secondary = self._extract_tops_geometry(self.secondary_metadata)

        # Established catalog mapping; no additional catalog required.
        selected_bursts = {218: 2, 219: 3, 220: 4}

        ref_steering = reference["steering_rate"]
        sec_steering = secondary["steering_rate"]

        self.logger.info(
            "Steering rate | Reference=%.9f deg/s | Secondary=%.9f deg/s",
            ref_steering,
            sec_steering,
        )

        if not math.isclose(ref_steering, sec_steering, rel_tol=1e-6):
            raise ValueError("Incompatible azimuth steering rates")

        if not math.isclose(
            reference["range_sampling_rate"],
            secondary["range_sampling_rate"],
            rel_tol=1e-6,
        ):
            raise ValueError("Incompatible range sampling rates")

        for label, geometry in (
            ("Reference", reference),
            ("Secondary", secondary),
        ):
            self.logger.info(
                "%s | Doppler records=%d | FM-rate records=%d",
                label,
                len(geometry["doppler_records"]),
                len(geometry["fm_records"]),
            )

            # Evaluate models across the full recorded range swath.
            sample_indices = np.linspace(
                0,
                geometry["samples_per_burst"] - 1,
                num=256,
            )

            range_times = (
                geometry["slant_range_time"]
                + sample_indices / geometry["range_sampling_rate"]
            )

            for burst_id, index in selected_bursts.items():
                if index >= len(geometry["burst_times"]):
                    raise ValueError(f"{label}: burst {burst_id} index out of range")

                burst_start = geometry["burst_times"][index]
                burst_midpoint = burst_start + (
                    datetime.fromtimestamp(0, tz=burst_start.tzinfo)
                    - datetime.fromtimestamp(0, tz=burst_start.tzinfo)
                )
                # Midpoint in azimuth time, not range time.
                from datetime import timedelta

                burst_midpoint = burst_start + timedelta(
                    seconds=(
                        geometry["lines_per_burst"] * geometry["azimuth_interval"] / 2
                    )
                )

                dc_record = self._nearest_tops_record(
                    geometry["doppler_records"],
                    burst_midpoint,
                )

                fm_record = self._nearest_tops_record(
                    geometry["fm_records"],
                    burst_midpoint,
                )

                doppler = self._evaluate_polynomial(
                    dc_record["coefficients"],
                    range_times - dc_record["t0"],
                )

                fm_rate = self._evaluate_polynomial(
                    fm_record["coefficients"],
                    range_times - fm_record["t0"],
                )

                if not np.all(np.isfinite(doppler)):
                    raise ValueError(f"{label}: burst {burst_id} invalid Doppler")

                if not np.all(np.isfinite(fm_rate)):
                    raise ValueError(f"{label}: burst {burst_id} invalid FM-rate")

                if np.any(fm_rate == 0):
                    raise ValueError(f"{label}: burst {burst_id} zero FM-rate")

                if dc_record["above_threshold"]:
                    self.logger.warning(
                        "%s burst %d: Doppler RMS threshold exceeded",
                        label,
                        burst_id,
                    )

                self.logger.info(
                    "%s | Burst %d | Doppler centroid: "
                    "[%.3f, %.3f] Hz | "
                    "FM-rate: [%.3f, %.3f] Hz/s | "
                    "DC RMS error: %.3f Hz",
                    label,
                    burst_id,
                    float(doppler.min()),
                    float(doppler.max()),
                    float(fm_rate.min()),
                    float(fm_rate.max()),
                    dc_record["rms_error"],
                )

        self.logger.info("TOPS Doppler metadata and numerical checks: PASSED")

    def validate_authoritative_burst_identity(
        self,
        reference_catalog: list[dict],
        secondary_catalog: list[dict],
        selected_ids: tuple[int, ...] = (218, 219, 220),
    ) -> None:
        self.logger.info("Validating authoritative burst identity...")

        reference_times = self._extract_burst_timing(self.reference_metadata)
        secondary_times = self._extract_burst_timing(self.secondary_metadata)

        reference_mapping = self._validate_catalog(
            reference_catalog,
            reference_times,
            "Reference",
        )

        secondary_mapping = self._validate_catalog(
            secondary_catalog,
            secondary_times,
            "Secondary",
        )

        for burst_id in selected_ids:
            if burst_id not in reference_mapping:
                raise ValueError(f"Reference missing burst ID {burst_id}")

            if burst_id not in secondary_mapping:
                raise ValueError(f"Secondary missing burst ID {burst_id}")

            ref_index = reference_mapping[burst_id]
            sec_index = secondary_mapping[burst_id]

            self.logger.info(
                "Burst %s | Reference index: %s | Secondary index: %s",
                burst_id,
                ref_index,
                sec_index,
            )

        self.logger.info("Catalog burst correspondence: PASSED")

    def inspect_small_amplitude_window(self):

        col_off = 12000
        row_off = 5500
        height = 1024
        width = 1024

        # Small subset, not the entire SLC
        window = Window(
            col_off=col_off,  # type: ignore
            row_off=row_off,  # type: ignore
            width=width,  # type: ignore
            height=height,  # type: ignore
        )

        with rasterio.open(self.tiff_path) as src:
            slc = src.read(1, window=window)

        zero_fraction_by_row = np.mean(slc == 0, axis=1)

        for row in (500, 540, 560, 580, 620):
            print(
                f"Global line {5500 + row}: "
                f"{zero_fraction_by_row[row]:.1%} zero samples"
            )

        amplitude = np.abs(slc.astype(np.complex64))

        amplitude_db = np.full(
            amplitude.shape,
            np.nan,
            dtype=np.float32,
        )

        nonzero = amplitude > 0

        amplitude_db[nonzero] = 20 * np.log10(amplitude[nonzero])

        valid_db = amplitude_db[np.isfinite(amplitude_db)]
        if valid_db.size == 0:
            raise ValueError("No nonzero finite amplitudes in the selected window")

        # Analyze amplitude of the SLC
        plt.imshow(
            amplitude_db,
            cmap="gray",
            origin="upper",
            extent=(12000, 13024, 6524, 5500),
            vmin=np.percentile(valid_db, 2),  # type: ignore
            vmax=np.percentile(valid_db, 98),  # type: ignore
        )

        plt.axhline(
            6064,
            color="red",
            linestyle="--",
            label="Expected burst boundary",
        )

        plt.legend()

        plt.title("Sentinel-1 IW3 VV - SLC amplitude (dB)")
        plt.xlabel("Range sample")
        plt.ylabel("Azimuth line")
        plt.colorbar(label="Amplitude (dB)")
        plt.tight_layout()
        plt.show()

        # Analyze wrapped phase of the SLC
        phase = np.angle(slc.astype(np.complex64))
        phase[slc == 0] = np.nan

        plt.imshow(
            phase,
            cmap="twilight",
            extent=(12000, 13024, 6524, 5500),
            vmin=-np.pi,
            vmax=np.pi,
        )
        plt.axhline(
            6064,
            color="red",
            linestyle="--",
            label="Expected burst boundary",
        )

        plt.colorbar(label="Phase (radians)")
        plt.title("Sentinel-1 SLC wrapped phase")
        plt.tight_layout()
        plt.show()

        # Analyze zero-sample distribution across the burst boundary
        zero_fraction_by_row = np.mean(slc == 0, axis=1)

        global_lines = np.arange(slc.shape[0]) + window.row_off

        plt.figure(figsize=(10, 4))
        plt.plot(global_lines, zero_fraction_by_row)

        plt.axvline(
            6064,
            color="red",
            linestyle="--",
            label="Expected burst boundary",
        )

        plt.xlabel("Global azimuth line")
        plt.ylabel("Fraction of zero samples")
        plt.title("Zero-sample distribution across burst boundary")
        plt.legend()
        plt.tight_layout()
        plt.show()

    @staticmethod
    def _build_burst_mask(
        first: list[int],
        last: list[int],
        samples_per_burst: int,
    ) -> np.ndarray:
        first_arr = np.asarray(first)
        last_arr = np.asarray(last)

        assert first_arr.shape == last_arr.shape

        columns = np.arange(samples_per_burst)[None, :]

        mask = (
            (first_arr[:, None] >= 0)
            & (last_arr[:, None] >= first_arr[:, None])
            & (columns >= first_arr[:, None])
            & (columns <= last_arr[:, None])
        )

        return mask

    def extract_annotation_metadata(self, annotation_path) -> dict[str, object]:
        root = ET.parse(annotation_path).getroot()

        burst_elements = root.findall("swathTiming/burstList/burst")

        metadata = {
            "mission": root.findtext("adsHeader/missionId"),
            "swath": root.findtext("adsHeader/swath"),
            "polarization": root.findtext("adsHeader/polarisation"),
            "start_time": root.findtext("adsHeader/startTime"),
            "stop_time": root.findtext("adsHeader/stopTime"),
            "lines_per_burst": root.findtext("swathTiming/linesPerBurst"),
            "samples_per_burst": root.findtext("swathTiming/samplesPerBurst"),
            "burst_count": len(burst_elements),
            "burst_azimuth_times": [
                burst.findtext("azimuthTime") for burst in burst_elements
            ],
        }

        required_fields = (
            "mission",
            "swath",
            "polarization",
            "start_time",
            "stop_time",
            "lines_per_burst",
            "samples_per_burst",
        )

        self.check_required_fields(required_fields, metadata)

        if metadata["burst_count"] == 0:
            raise ValueError("No bursts found in annotation XML")

        if any(
            time is None or not time.strip() for time in metadata["burst_azimuth_times"]
        ):
            raise ValueError("Missing burst azimuth timestamps")

        metadata["lines_per_burst"] = int(metadata["lines_per_burst"])
        metadata["samples_per_burst"] = int(metadata["samples_per_burst"])

        return metadata

    def check_required_fields(
        self,
        required_fields: tuple[str, ...],
        metadata: dict[str, object],
    ) -> None:
        missing = [
            field
            for field in required_fields
            if metadata.get(field) is None or metadata.get(field) == ""
        ]

        if missing:
            raise ValueError(f"Missing required annotation fields: {missing}")

    def _extract_geolocation_points(self, metadata) -> list[dict[str, int | float]]:
        """Extract radar-grid positions and geographic coordinates."""

        root = ET.parse(metadata).getroot()

        point_elements = root.findall(
            "geolocationGrid/geolocationGridPointList/geolocationGridPoint"
        )

        if not point_elements:
            raise ValueError("No geolocation grid points found in annotation XML")

        points = []

        for index, element in enumerate(point_elements):
            values = {
                "line": element.findtext("line"),
                "pixel": element.findtext("pixel"),
                "latitude": element.findtext("latitude"),
                "longitude": element.findtext("longitude"),
            }

            missing = [
                name
                for name, value in values.items()
                if value is None or not value.strip()
            ]

            if missing:
                raise ValueError(
                    f"Geolocation point {index} has missing fields: {missing}"
                )

            point = {
                "line": int(values["line"]),
                "pixel": int(values["pixel"]),
                "latitude": float(values["latitude"]),
                "longitude": float(values["longitude"]),
            }

            if not (-90 <= point["latitude"] <= 90):
                raise ValueError(f"Invalid latitude at point {index}")

            if not (-180 <= point["longitude"] <= 180):
                raise ValueError(f"Invalid longitude at point {index}")

            points.append(point)

        self.logger.info(
            "Extracted %d geolocation grid points",
            len(points),
        )

        return points

    def _summarize_selected_bursts(
        self,
        points: list[dict[str, int | float]],
        lines_per_burst: int,
        burst_indices: dict[int, int],
    ) -> dict[int, dict]:
        summaries = {}

        for burst_id, burst_index in burst_indices.items():
            row_start = burst_index * lines_per_burst
            row_boundary = row_start + lines_per_burst

            burst_points = [
                point for point in points if row_start <= point["line"] <= row_boundary
            ]

            if not burst_points:
                raise ValueError(f"No geolocation points for burst {burst_id}")

            expected_lines = {row_start, row_boundary}

            actual_lines = {point["line"] for point in burst_points}

            if actual_lines != expected_lines:
                raise ValueError(
                    f"Burst {burst_id}: unexpected geolocation lines. "
                    f"Expected {expected_lines}, got {actual_lines}"
                )

            summaries[burst_id] = {
                "point_count": len(burst_points),
                "sampled_lines": sorted({point["line"] for point in burst_points}),
                "min_latitude": min(point["latitude"] for point in burst_points),
                "max_latitude": max(point["latitude"] for point in burst_points),
                "min_longitude": min(point["longitude"] for point in burst_points),
                "max_longitude": max(point["longitude"] for point in burst_points),
            }

        return summaries

    def _summary_geolocation_points(
        self, points: list[dict[str, float]], label: str
    ) -> None:
        latitudes = [point["latitude"] for point in points]
        longitudes = [point["longitude"] for point in points]

        summary = {
            "label": label,
            "point_count": len(points),
            "min_latitude": min(latitudes),
            "max_latitude": max(latitudes),
            "min_longitude": min(longitudes),
            "max_longitude": max(longitudes),
        }

        self.logger.info("Geolocation summary: %s", summary)

    def _extract_radar_geometry(self, metadata) -> dict[str, str | float]:
        root = ET.parse(metadata).getroot()

        paths = {
            "radar_frequency": ("generalAnnotation/productInformation/radarFrequency"),
            "range_sampling_rate": (
                "generalAnnotation/productInformation/rangeSamplingRate"
            ),
            "azimuth_time_interval": (
                "imageAnnotation/imageInformation/azimuthTimeInterval"
            ),
            "slant_range_time": ("imageAnnotation/imageInformation/slantRangeTime"),
            "range_pixel_spacing": (
                "imageAnnotation/imageInformation/rangePixelSpacing"
            ),
            "azimuth_pixel_spacing": (
                "imageAnnotation/imageInformation/azimuthPixelSpacing"
            ),
            "incidence_angle_mid_swath": (
                "imageAnnotation/imageInformation/incidenceAngleMidSwath"
            ),
        }

        geometry = {}

        for field, path in paths.items():
            value = root.findtext(path)

            if value is None or not value.strip():
                raise ValueError(
                    f"Missing radar geometry field: {field} (XML path: {path})"
                )

            geometry[field] = float(value)

        return geometry

    def _extract_orbit_metadata(self, annotation_path: Path) -> dict:
        root = ET.parse(annotation_path).getroot()

        absolute_orbit = root.findtext("adsHeader/absoluteOrbitNumber")
        orbit_direction = root.findtext("generalAnnotation/productInformation/pass")
        mission = root.findtext("adsHeader/missionId")

        if absolute_orbit is None or not absolute_orbit.strip():
            raise ValueError("Missing absolute orbit number")

        if orbit_direction is None or not orbit_direction.strip():
            raise ValueError("Missing orbit direction")

        if mission != "S1A":
            raise ValueError(f"Relative orbit calculation not configured for {mission}")

        absolute_orbit = int(absolute_orbit)
        relative_orbit = ((absolute_orbit - 73) % 175) + 1
        orbit_direction = orbit_direction.strip().upper()

        if orbit_direction not in {"ASCENDING", "DESCENDING"}:
            raise ValueError(f"Invalid orbit direction: {orbit_direction}")

        return {
            "absolute_orbit": absolute_orbit,
            "relative_orbit": relative_orbit,
            "orbit_direction": orbit_direction,
        }

    def _compare_radar_geometry(
        self,
        reference: dict[str, float],
        secondary: dict[str, float],
    ) -> None:
        self.logger.info("Comparing radar acquisition geometry...")

        comparable_fields = (
            "radar_frequency",
            "range_sampling_rate",
            "azimuth_time_interval",
            "range_pixel_spacing",
            "azimuth_pixel_spacing",
        )

        acquisition_specific_fields = (
            "slant_range_time",
            "incidence_angle_mid_swath",
        )

        required_fields = (
            *comparable_fields,
            *acquisition_specific_fields,
        )

        # Validate completeness and numeric values.
        for label, geometry in (
            ("Reference", reference),
            ("Secondary", secondary),
        ):
            for field in required_fields:
                value = geometry.get(field)

                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(
                        f"{label}: missing or invalid "
                        f"radar parameter '{field}': {value}"
                    )

        # Compare sampling/configuration parameters.
        for field in comparable_fields:
            ref_value = reference[field]
            sec_value = secondary[field]

            difference = sec_value - ref_value

            self.logger.info(
                "%s | Reference: %.12g | Secondary: %.12g | Difference: %.12g",
                field,
                ref_value,
                sec_value,
                difference,
            )

            if not math.isclose(
                ref_value,
                sec_value,
                rel_tol=1e-6,
                abs_tol=0.0,
            ):
                raise ValueError(
                    f"Incompatible radar parameter '{field}': "
                    f"reference={ref_value}, "
                    f"secondary={sec_value}"
                )

        # Report acquisition-specific parameters.
        for field in acquisition_specific_fields:
            ref_value = reference[field]
            sec_value = secondary[field]

            self.logger.info(
                "%s | Reference: %.12g | Secondary: %.12g | Difference: %.12g",
                field,
                ref_value,
                sec_value,
                sec_value - ref_value,
            )

    def _extract_burst_timing(
        self,
        annotation_path: Path,
    ) -> list[datetime]:
        root = ET.parse(annotation_path).getroot()

        burst_elements = root.findall("swathTiming/burstList/burst")

        if not burst_elements:
            raise ValueError(f"No bursts found in {annotation_path}")

        burst_times = []

        for index, burst in enumerate(burst_elements):
            value = burst.findtext("azimuthTime")

            if not value:
                raise ValueError(f"Missing azimuthTime for burst index {index}")

            burst_times.append(datetime.fromisoformat(value))

        return burst_times

    def _validate_catalog(
        self,
        records: list[dict],
        burst_times: list[datetime],
        label: str,
    ) -> dict[int, int]:
        mapping = {}

        for record in records:
            burst_id = int(record["burst_id"])
            index = int(record["annotation_index"])

            if burst_id in mapping:
                raise ValueError(f"{label}: duplicate burst ID {burst_id}")

            if not 0 <= index < len(burst_times):
                raise ValueError(
                    f"{label}: burst {burst_id} has invalid annotation index {index}"
                )

            if index in mapping.values():
                raise ValueError(f"{label}: duplicate annotation index {index}")

            mapping[burst_id] = index

        return mapping

    @staticmethod
    def _required_xml_text(element: ET.Element, path: str) -> str:
        value = element.findtext(path)
        if value is None or not value.strip():
            raise ValueError(f"Missing XML field: {path}")
        return value.strip()

    @staticmethod
    def _parse_xml_time(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _evaluate_polynomial(
        coefficients: list[float],
        delta_time: np.ndarray,
    ) -> np.ndarray:
        # XML coefficients are ordered c0, c1, c2, ...
        return np.polynomial.polynomial.polyval(delta_time, coefficients)

    def _extract_tops_geometry(self, annotation_path: Path) -> dict:
        root = ET.parse(annotation_path).getroot()
        required = self._required_xml_text

        steering_rate = float(
            required(
                root,
                "generalAnnotation/productInformation/azimuthSteeringRate",
            )
        )

        slant_range_time = float(
            required(
                root,
                "imageAnnotation/imageInformation/slantRangeTime",
            )
        )

        range_sampling_rate = float(
            required(
                root,
                "generalAnnotation/productInformation/rangeSamplingRate",
            )
        )

        samples_per_burst = int(required(root, "swathTiming/samplesPerBurst"))

        azimuth_interval = float(
            required(
                root,
                "imageAnnotation/imageInformation/azimuthTimeInterval",
            )
        )

        lines_per_burst = int(required(root, "swathTiming/linesPerBurst"))

        bursts = root.findall("swathTiming/burstList/burst")
        if not bursts:
            raise ValueError("No bursts in annotation XML")

        burst_times = [
            self._parse_xml_time(required(burst, "azimuthTime")) for burst in bursts
        ]

        doppler_records = []
        for element in root.findall("dopplerCentroid/dcEstimateList/dcEstimate"):
            coefficients = [
                float(value) for value in required(element, "dataDcPolynomial").split()
            ]

            doppler_records.append(
                {
                    "azimuth_time": self._parse_xml_time(
                        required(element, "azimuthTime")
                    ),
                    "t0": float(required(element, "t0")),
                    "coefficients": coefficients,
                    "rms_error": float(required(element, "dataDcRmsError")),
                    "above_threshold": required(
                        element, "dataDcRmsErrorAboveThreshold"
                    ).lower()
                    == "true",
                }
            )

        fm_records = []
        for element in root.findall(
            "generalAnnotation/azimuthFmRateList/azimuthFmRate"
        ):
            polynomial = element.findtext("azimuthFmRatePolynomial")

            if polynomial is not None and polynomial.strip():
                coefficients = [float(value) for value in polynomial.split()]
            else:
                # Supported alternate Sentinel-1 annotation representation.
                coefficients = [
                    float(required(element, field)) for field in ("c0", "c1", "c2")
                ]

            fm_records.append(
                {
                    "azimuth_time": self._parse_xml_time(
                        required(element, "azimuthTime")
                    ),
                    "t0": float(required(element, "t0")),
                    "coefficients": coefficients,
                }
            )

        if not doppler_records or not fm_records:
            raise ValueError("Missing Doppler centroid or azimuth FM-rate records")

        scalar_values = {
            "steering_rate": steering_rate,
            "slant_range_time": slant_range_time,
            "range_sampling_rate": range_sampling_rate,
            "azimuth_interval": azimuth_interval,
        }

        for name, value in scalar_values.items():
            if not math.isfinite(value):
                raise ValueError(f"Non-finite {name}: {value}")

        if range_sampling_rate <= 0 or azimuth_interval <= 0:
            raise ValueError("Sampling intervals must be positive")

        if samples_per_burst <= 0 or lines_per_burst <= 0:
            raise ValueError("Invalid burst dimensions")

        for name, records in (
            ("Doppler", doppler_records),
            ("FM-rate", fm_records),
        ):
            times = [record["azimuth_time"] for record in records]
            if times != sorted(times):
                raise ValueError(f"{name} records are not time ordered")

            for record in records:
                numbers = [record["t0"], *record["coefficients"]]
                if not all(math.isfinite(value) for value in numbers):
                    raise ValueError(f"Non-finite {name} coefficients")

        return {
            "steering_rate": steering_rate,
            "slant_range_time": slant_range_time,
            "range_sampling_rate": range_sampling_rate,
            "samples_per_burst": samples_per_burst,
            "azimuth_interval": azimuth_interval,
            "lines_per_burst": lines_per_burst,
            "burst_times": burst_times,
            "doppler_records": doppler_records,
            "fm_records": fm_records,
        }

    @staticmethod
    def _nearest_tops_record(
        records: list[dict],
        azimuth_time: datetime,
    ) -> dict:
        return min(
            records,
            key=lambda record: abs(
                (record["azimuth_time"] - azimuth_time).total_seconds()
            ),
        )


if __name__ == "__main__":
    LoggingManager.setup(debug=False)

    annotation_path_16_january = Path(
        "data/raw/annotation/216aff3b-f44e-4d82-9e71-f28a43fce10d/s1a-iw3-slc-vv-20250116t181040-20250116t181106-057473-0713a0-006.xml"
    )
    tiff_path_16_january = Path(
        "data/raw/measurement/216aff3b-f44e-4d82-9e71-f28a43fce10d/s1a-iw3-slc-vv-20250116t181040-20250116t181106-057473-0713a0-006.tiff"
    )

    tiff_path_04_january = Path(
        "data/raw/measurement/4770594e-9ee5-432a-9552-22f1e27efd61/s1a-iw3-slc-vv-20250104t181041-20250104t181107-057298-070cb8-006.tiff"
    )
    annotation_path_04_january = Path(
        "data/raw/annotation/s1a-iw3-slc-vv-20250104t181041-20250104t181107-057298-070cb8-006.xml"
    )

    explorer = ExploreSARTiff(
        tiff_path=tiff_path_04_january,
        reference_metadata=annotation_path_04_january,
        secondary_metadata=annotation_path_16_january,
    )

    explorer.run()
