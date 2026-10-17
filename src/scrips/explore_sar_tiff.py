import logging
import xml.etree.ElementTree as ET
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
        annotation_path: Path,
    ):
        self.logger = logging.getLogger(__name__)

        self.tiff_path = tiff_path
        self.annotation_path = annotation_path

    def run(self):
        self.inspect_metadata()
        mask = self.inspect_annotation()
        self.compare_mask_with_tiff(mask)
        self.inspect_boundary(mask)
        # self._inspect_small_amplitude_window()

    def inspect_metadata(self):
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

        self.logger.info("\nInspecting annotation XML: %s", self.annotation_path)
        root = ET.parse(self.annotation_path).getroot()

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

        plt.title("Sentinel-1 IW3 VV — SLC amplitude (dB)")
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


if __name__ == "__main__":
    LoggingManager.setup(debug=False)

    # tiff_path_16_january = Path("data/raw/annotation/216aff3b-f44e-4d82-9e71-f28a43fce10d/s1a-iw3-slc-vv-20250116t181040-20250116t181106-057473-0713a0-006.xml")
    # annotation_path_16_january = Path("data/raw/measurement/216aff3b-f44e-4d82-9e71-f28a43fce10d/s1a-iw3-slc-vv-20250116t181040-20250116t181106-057473-0713a0-006.tiff")

    tiff_path_04_january = Path(
        "data/raw/measurement/4770594e-9ee5-432a-9552-22f1e27efd61/s1a-iw3-slc-vv-20250104t181041-20250104t181107-057298-070cb8-006.tiff"
    )
    annotation_path_04_january = Path(
        "data/raw/annotation/s1a-iw3-slc-vv-20250104t181041-20250104t181107-057298-070cb8-006.xml"
    )

    explorer = ExploreSARTiff(
        tiff_path=tiff_path_04_january, annotation_path=annotation_path_04_january
    )

    explorer.run()
