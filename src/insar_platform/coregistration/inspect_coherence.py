"""Inspect coherence in a SNAP BEAM-DIMAP product.

Calculate coherence statistics without loading the entire raster into memory.

Outputs:
    coherence_statistics.json
    coherence_histogram.npz
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning


@dataclass(frozen=True)
class CoherenceReport:
    input_dim: str
    coherence_band: str

    total_pixels: int
    valid_pixels: int
    excluded_pixels: int

    mean: float
    median: float
    minimum: float
    maximum: float

    percent_above_03: float
    percent_above_06: float
    percent_above_08: float

    median_approximate: bool
    percent_excluded: float = 0.0

    masked_pixels: int = 0
    nonfinite_pixels: int = 0
    invalid_range_pixels: int = 0


def _find_coherence_raster(
    input_dim: Path,
    coherence_band: str | None = None,
) -> Path:
    """Find a physical coherence raster in the SNAP .data directory."""
    data_dir = input_dim.with_suffix(".data")

    if not data_dir.is_dir():
        raise FileNotFoundError(f"SNAP .data directory not found: {data_dir}")

    candidates = sorted(
        path
        for path in data_dir.rglob("*.img")
        if path.is_file()
        and path.stem.lower().startswith(("coh_", "coherence"))
        and "tie_point_grids"
        not in {part.lower() for part in path.relative_to(data_dir).parts[:-1]}
    )

    if coherence_band is not None:
        candidates = [
            path
            for path in candidates
            if path.stem.casefold() == coherence_band.casefold()
        ]

    if len(candidates) != 1:
        available = [path.name for path in candidates]

        raise ValueError(
            "Expected exactly one coherence raster. "
            f"Found {len(candidates)} in {data_dir}: {available}. "
            "Specify coherence_band when multiple bands exist."
        )

    return candidates[0]


def inspect_coherence(
    input_dim: Path,
    output_dir: Path,
    logger: logging.Logger,
    *,
    histogram_bins: int = 10000,
    coherence_band: str | None = None,
) -> CoherenceReport:
    """Calculate coherence statistics using block-wise raster processing.

    Only unmasked, finite values within [0, 1] contribute to
    coherence statistics.

    Excluded pixels are classified as:
        - masked_pixels
        - nonfinite_pixels
        - invalid_range_pixels

    The median is estimated using a fixed-width histogram.
    """
    input_dim = Path(input_dim).resolve()
    output_dir = Path(output_dir).resolve()

    if input_dim.suffix.lower() != ".dim" or not input_dim.is_file():
        raise FileNotFoundError(f"SNAP .dim product not found: {input_dim}")

    if histogram_bins < 2:
        raise ValueError("histogram_bins must be >= 2")

    coherence_file = _find_coherence_raster(
        input_dim,
        coherence_band=coherence_band,
    )

    logger.info(
        "Inspecting coherence raster: %s",
        coherence_file,
    )

    histogram = np.zeros(
        histogram_bins,
        dtype=np.int64,
    )

    total_pixels = 0
    valid_pixels = 0

    masked_pixels = 0
    nonfinite_pixels = 0
    invalid_range_pixels = 0

    value_sum = 0.0

    minimum = float("inf")
    maximum = float("-inf")

    count_03 = 0
    count_06 = 0
    count_08 = 0

    # Radar geometry products may lack a geotransform.
    # This is expected and does not affect coherence statistics.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=NotGeoreferencedWarning,
        )

        with rasterio.open(coherence_file) as src:
            if src.count != 1:
                raise ValueError(
                    f"Expected one raster band, found {src.count}: {coherence_file}"
                )

            logger.info(
                "Raster dimensions: %d x %d",
                src.width,
                src.height,
            )

            for _, window in src.block_windows(1):
                block = src.read(
                    1,
                    window=window,
                    masked=True,
                )

                total_pixels += block.size

                # 1. Identify masked pixels.
                mask = np.ma.getmaskarray(block)

                masked_pixels += int(np.count_nonzero(mask))

                # 2. Extract unmasked values.
                values = np.asarray(
                    block.data[~mask],
                    dtype=np.float64,
                )

                # 3. Identify non-finite values.
                finite_mask = np.isfinite(values)

                nonfinite_pixels += int(np.count_nonzero(~finite_mask))

                values = values[finite_mask]

                # 4. Identify coherence values outside [0, 1].
                range_mask = (values >= 0.0) & (values <= 1.0)

                invalid_range_pixels += int(np.count_nonzero(~range_mask))

                values = values[range_mask]

                if values.size == 0:
                    continue

                # 5. Update valid pixel count.
                valid_pixels += int(values.size)

                # 6. Calculate sum for mean coherence.
                value_sum += float(
                    np.sum(
                        values,
                        dtype=np.float64,
                    )
                )

                # 7. Update minimum and maximum.
                minimum = min(
                    minimum,
                    float(values.min()),
                )

                maximum = max(
                    maximum,
                    float(values.max()),
                )

                # 8. Count pixels above coherence thresholds.
                count_03 += int(np.count_nonzero(values > 0.3))

                count_06 += int(np.count_nonzero(values > 0.6))

                count_08 += int(np.count_nonzero(values > 0.8))

                # 9. Accumulate histogram.
                histogram += np.histogram(
                    values,
                    bins=histogram_bins,
                    range=(0.0, 1.0),
                )[0]

    if valid_pixels == 0:
        raise ValueError(f"No valid coherence pixels in {coherence_file}")

    excluded_pixels = total_pixels - valid_pixels

    accounted_exclusions = masked_pixels + nonfinite_pixels + invalid_range_pixels

    if excluded_pixels != accounted_exclusions:
        raise RuntimeError(
            "Pixel accounting mismatch: "
            f"excluded={excluded_pixels}, "
            f"accounted={accounted_exclusions}"
        )

    # Approximate median using histogram.
    cumulative = np.cumsum(histogram)

    middle_left = (valid_pixels - 1) // 2
    middle_right = valid_pixels // 2

    left_idx = int(
        np.searchsorted(
            cumulative,
            middle_left + 1,
        )
    )

    right_idx = int(
        np.searchsorted(
            cumulative,
            middle_right + 1,
        )
    )

    median = ((left_idx + 0.5) + (right_idx + 0.5)) / (2 * histogram_bins)

    report = CoherenceReport(
        input_dim=str(input_dim),
        coherence_band=str(coherence_file),
        total_pixels=total_pixels,
        valid_pixels=valid_pixels,
        excluded_pixels=excluded_pixels,
        mean=value_sum / valid_pixels,
        median=median,
        minimum=minimum,
        maximum=maximum,
        percent_above_03=100.0 * count_03 / valid_pixels,
        percent_above_06=100.0 * count_06 / valid_pixels,
        percent_above_08=100.0 * count_08 / valid_pixels,
        median_approximate=True,
        percent_excluded=100.0 * excluded_pixels / total_pixels,
        masked_pixels=masked_pixels,
        nonfinite_pixels=nonfinite_pixels,
        invalid_range_pixels=invalid_range_pixels,
    )

    # Create output directory.
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Save JSON statistics.
    report_path = output_dir / "coherence_statistics.json"

    report_path.write_text(
        json.dumps(
            asdict(report),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Save histogram for later visualization.
    histogram_path = output_dir / "coherence_histogram.npz"

    np.savez_compressed(
        histogram_path,
        counts=histogram,
        edges=np.linspace(
            0.0,
            1.0,
            histogram_bins + 1,
        ),
    )

    # Log results.
    logger.info("Mean coherence:   %.6f", report.mean)

    logger.info(
        "Median coherence: %.6f (histogram approximation)",
        report.median,
    )

    logger.info("Minimum:          %.6f", report.minimum)
    logger.info("Maximum:          %.6f", report.maximum)

    logger.info(
        "Pixels > 0.3:     %.2f%%",
        report.percent_above_03,
    )

    logger.info(
        "Pixels > 0.6:     %.2f%%",
        report.percent_above_06,
    )

    logger.info(
        "Pixels > 0.8:     %.2f%%",
        report.percent_above_08,
    )

    logger.info(
        "Valid pixels:     %d / %d",
        report.valid_pixels,
        report.total_pixels,
    )

    logger.info(
        "Excluded pixels:  %d (%.2f%%)",
        report.excluded_pixels,
        report.percent_excluded,
    )

    logger.info("Masked pixels:    %d", report.masked_pixels)
    logger.info("Nonfinite pixels: %d", report.nonfinite_pixels)
    logger.info("Invalid range:    %d", report.invalid_range_pixels)

    if report.invalid_range_pixels:
        logger.warning(
            "Coherence contains %d values outside [0, 1]",
            report.invalid_range_pixels,
        )

    if report.nonfinite_pixels:
        logger.warning(
            "Coherence contains %d unmasked non-finite values",
            report.nonfinite_pixels,
        )

    logger.info(
        "Saved coherence report: %s",
        report_path,
    )

    logger.info(
        "Saved coherence histogram: %s",
        histogram_path,
    )

    return report


def inspect_coherence_pipeline(
    interferogram_dim: Path,
    output_dir: Path,
    logger: logging.Logger,
    *,
    histogram_bins: int = 10000,
    coherence_band: str | None = None,
) -> CoherenceReport:
    """Tracked application entry point for coherence statistics."""
    logger.info(
        "Inspecting coherence in %s",
        interferogram_dim,
    )

    return inspect_coherence(
        input_dim=Path(interferogram_dim),
        output_dir=Path(output_dir),
        histogram_bins=histogram_bins,
        coherence_band=coherence_band,
        logger=logger,
    )
