from dataclasses import dataclass
from logging import Logger
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from insar_platform.ingestion.annotation import AnnotationBurst


@dataclass(frozen=True)
class MaskValidationResult:
    burst_index: int
    row_off: int
    col_off: int
    height: int
    width: int
    valid_nonzero: int
    valid_zero: int
    invalid_nonzero: int
    invalid_zero: int


@dataclass(frozen=True)
class BoundaryValidationResult:
    boundary_row: int
    row_off: int
    col_off: int
    height: int
    width: int
    valid_nonzero: int
    valid_zero: int
    invalid_nonzero: int
    invalid_zero: int
    zero_fraction_by_row: list[float]
    invalid_fraction_by_row: list[float]


class SARMeasurementValidator:
    def __init__(
        self,
        measurement_path: Path,
        logger: Logger,
        max_invalid_nonzero_fraction: float = 0.0,
    ):
        self.measurement_path = measurement_path
        self.logger = logger
        self.max_invalid_nonzero_fraction = max_invalid_nonzero_fraction

    def compare_mask_with_tiff(
        self,
        annotation_bursts: list[AnnotationBurst],
        burst_index: int,
        lines_per_burst: int,
        row_offset_in_burst: int = 500,
        col_off: int = 12000,
        height: int = 256,
        width: int = 1024,
    ) -> MaskValidationResult:
        if not 0 <= burst_index < len(annotation_bursts):
            raise ValueError(f"Invalid burst index: {burst_index}")

        burst = annotation_bursts[burst_index]

        if lines_per_burst <= 0:
            raise ValueError("lines_per_burst must be positive")

        if len(burst.first_valid_samples) != lines_per_burst:
            raise ValueError("Annotation burst dimension mismatch")

        if len(burst.last_valid_samples) != lines_per_burst:
            raise ValueError("Annotation burst dimension mismatch")

        if row_offset_in_burst < 0 or col_off < 0 or height <= 0 or width <= 0:
            raise ValueError("Invalid window dimensions or offsets")

        if row_offset_in_burst + height > lines_per_burst:
            raise ValueError("Window crosses a burst boundary")

        row_off = burst_index * lines_per_burst + row_offset_in_burst

        window = Window(
            col_off=col_off,  # type: ignore
            row_off=row_off,  # type: ignore
            width=width,  # type: ignore
            height=height,  # type: ignore
        )

        with rasterio.open(self.measurement_path) as src:
            if src.count != 1:
                raise ValueError(f"Expected one SLC band, found {src.count}")

            if src.dtypes[0] not in ("complex_int16", "complex64", "complex128"):
                raise ValueError(f"Expected complex SLC data, found {src.dtypes[0]}")

            expected_height = len(annotation_bursts) * lines_per_burst
            if src.height != expected_height:
                raise ValueError(
                    f"TIFF height mismatch: {src.height} != {expected_height}"
                )

            if row_off + height > src.height:
                raise ValueError("Window exceeds TIFF height")

            if col_off + width > src.width:
                raise ValueError("Window exceeds TIFF width")

            slc = src.read(1, window=window)

            if not np.all(np.isfinite(slc)):
                raise ValueError("Non-finite SLC samples in selected window")

        # Extract only the annotation rows corresponding to the window.
        first = np.asarray(
            burst.first_valid_samples[
                row_offset_in_burst : row_offset_in_burst + height
            ],
            dtype=np.int64,
        )

        last = np.asarray(
            burst.last_valid_samples[
                row_offset_in_burst : row_offset_in_burst + height
            ],
            dtype=np.int64,
        )

        # Global range-column indices of the TIFF window.
        columns = np.arange(
            col_off,
            col_off + width,
            dtype=np.int64,
        )

        valid_line = (first >= 0) & (last >= first) & (last < src.width)
        invalid_line = (first == -1) & (last == -1)
        if not np.all(valid_line | invalid_line):
            raise ValueError("Invalid annotation sample boundaries")

        # Broadcast annotation boundaries over the selected columns.
        valid = (
            (first[:, None] >= 0)
            & (last[:, None] >= first[:, None])
            & (columns[None, :] >= first[:, None])
            & (columns[None, :] <= last[:, None])
        )

        if slc.shape != valid.shape:
            raise ValueError(f"Shape mismatch: SLC={slc.shape}, mask={valid.shape}")

        zero = slc == 0

        counts = {
            "valid_nonzero": int(np.count_nonzero(valid & ~zero)),
            "valid_zero": int(np.count_nonzero(valid & zero)),
            "invalid_nonzero": int(np.count_nonzero(~valid & ~zero)),
            "invalid_zero": int(np.count_nonzero(~valid & zero)),
        }

        if sum(counts.values()) != slc.size:
            raise ValueError("Pixel classification is incomplete")

        result = MaskValidationResult(
            burst_index=burst_index,
            row_off=row_off,
            col_off=col_off,
            height=height,
            width=width,
            **counts,
        )

        self.logger.info(
            "Burst %d | TIFF rows [%d, %d) | Columns [%d, %d)",
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
                100 * count / slc.size,
            )

        return result

    def inspect_boundary(
        self,
        annotation_bursts: list[AnnotationBurst],
        boundary_index: int,
        lines_per_burst: int,
        col_off: int = 12000,
        height: int = 256,
        width: int = 1024,
    ) -> BoundaryValidationResult:
        """
        Inspect the boundary between annotation bursts
        boundary_index - 1 and boundary_index.
        """

        if not 1 <= boundary_index < len(annotation_bursts):
            raise ValueError(f"Invalid boundary index: {boundary_index}")

        if lines_per_burst <= 0 or height < 2 or width <= 0:
            raise ValueError("Invalid burst/window dimensions")

        if col_off < 0:
            raise ValueError("col_off must be non-negative")

        for index in (boundary_index - 1, boundary_index):
            burst = annotation_bursts[index]

            if (
                len(burst.first_valid_samples) != lines_per_burst
                or len(burst.last_valid_samples) != lines_per_burst
            ):
                raise ValueError(f"Burst {index}: validity array length mismatch")

        boundary_row = boundary_index * lines_per_burst

        # Center the window on the burst boundary.
        row_off = boundary_row - height // 2

        if row_off < 0:
            raise ValueError("Boundary window starts before TIFF")

        with rasterio.open(self.measurement_path) as src:
            if src.count != 1:
                raise ValueError(f"Expected one SLC band, found {src.count}")

            if src.dtypes[0] not in ("complex_int16", "complex64", "complex128"):
                raise ValueError(f"Expected complex SLC data, found {src.dtypes[0]}")

            expected_height = len(annotation_bursts) * lines_per_burst
            if src.height != expected_height:
                raise ValueError(
                    f"TIFF height mismatch: {src.height} != {expected_height}"
                )

            if row_off + height > src.height or col_off + width > src.width:
                raise ValueError("Boundary window exceeds TIFF dimensions")

            window = Window(
                col_off=col_off,  # type: ignore
                row_off=row_off,  # type: ignore
                width=width,  # type: ignore
                height=height,  # type: ignore
            )

            slc = src.read(1, window=window)

            if not np.all(np.isfinite(slc)):
                raise ValueError("Non-finite SLC samples in selected window")

        # Reconstruct the annotation mask for each TIFF row.
        global_rows = np.arange(
            row_off,
            row_off + height,
            dtype=np.int64,
        )

        columns = np.arange(
            col_off,
            col_off + width,
            dtype=np.int64,
        )

        first = np.empty(height, dtype=np.int64)
        last = np.empty(height, dtype=np.int64)

        for local_row, global_row in enumerate(global_rows):
            burst_index = int(global_row // lines_per_burst)
            burst_local_row = int(global_row % lines_per_burst)

            if burst_index >= len(annotation_bursts):
                raise ValueError(f"Missing annotation burst {burst_index}")

            burst = annotation_bursts[burst_index]

            first[local_row] = burst.first_valid_samples[burst_local_row]

            last[local_row] = burst.last_valid_samples[burst_local_row]

        valid_line = (first >= 0) & (last >= first) & (last < src.width)
        invalid_line = (first == -1) & (last == -1)
        if not np.all(valid_line | invalid_line):
            raise ValueError("Invalid annotation sample boundaries")

        valid = (
            (first[:, None] >= 0)
            & (last[:, None] >= first[:, None])
            & (columns[None, :] >= first[:, None])
            & (columns[None, :] <= last[:, None])
        )

        if slc.shape != valid.shape:
            raise ValueError(f"Shape mismatch: SLC={slc.shape}, mask={valid.shape}")

        # Explicitly verify mapping around the boundary.
        before = boundary_row - 1 - row_off
        after = boundary_row - row_off

        if not (0 <= before < height and 0 <= after < height):
            raise ValueError("Window does not include both sides of the boundary")

        previous_burst = annotation_bursts[boundary_index - 1]
        next_burst = annotation_bursts[boundary_index]

        expected_before = (
            (previous_burst.first_valid_samples[-1] >= 0)
            & (columns >= previous_burst.first_valid_samples[-1])
            & (columns <= previous_burst.last_valid_samples[-1])
        )

        expected_after = (
            (next_burst.first_valid_samples[0] >= 0)
            & (columns >= next_burst.first_valid_samples[0])
            & (columns <= next_burst.last_valid_samples[0])
        )

        if not np.array_equal(valid[before], expected_before):
            raise ValueError("Incorrect mask mapping before boundary")

        if not np.array_equal(valid[after], expected_after):
            raise ValueError("Incorrect mask mapping after boundary")

        zero = slc == 0

        counts = {
            "valid_nonzero": int(np.count_nonzero(valid & ~zero)),
            "valid_zero": int(np.count_nonzero(valid & zero)),
            "invalid_nonzero": int(np.count_nonzero(~valid & ~zero)),
            "invalid_zero": int(np.count_nonzero(~valid & zero)),
        }

        if sum(counts.values()) != slc.size:
            raise ValueError("Pixel classification is incomplete")

        zero_fraction = zero.mean(axis=1)
        invalid_fraction = (~valid).mean(axis=1)

        result = BoundaryValidationResult(
            boundary_row=boundary_row,
            row_off=row_off,
            col_off=col_off,
            height=height,
            width=width,
            **counts,
            zero_fraction_by_row=zero_fraction.tolist(),
            invalid_fraction_by_row=invalid_fraction.tolist(),
        )

        self.logger.info(
            "Boundary %d | TIFF rows [%d, %d) | columns [%d, %d)",
            boundary_index,
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
                100 * count / slc.size,
            )

        return result

    @staticmethod
    def validate_mask_consistency(
        counts: dict[str, int],
        *,
        require_invalid_pixels: bool = True,
        max_invalid_nonzero_fraction: float = 0.0,
    ) -> None:
        if not 0.0 <= max_invalid_nonzero_fraction <= 1.0:
            raise ValueError("Invalid mismatch threshold")

        total = sum(counts.values())

        if total <= 0:
            raise ValueError("Empty validation window")

        invalid_pixels = counts["invalid_nonzero"] + counts["invalid_zero"]

        if invalid_pixels == 0:
            if require_invalid_pixels:
                raise ValueError(
                    "Inconclusive validation: the selected window "
                    "contains no annotation-invalid pixels"
                )
            return

        invalid_nonzero_fraction = counts["invalid_nonzero"] / invalid_pixels

        if invalid_nonzero_fraction > max_invalid_nonzero_fraction:
            raise ValueError(
                "Annotation/TIFF consistency validation failed: "
                f"{counts['invalid_nonzero']} of {invalid_pixels} "
                "annotation-invalid pixels contain nonzero SLC values "
                f"({invalid_nonzero_fraction:.4%}; "
                f"maximum allowed: {max_invalid_nonzero_fraction:.4%})"
            )
