"""Inspect a SNAP BEAM-DIMAP coherence band without loading the entire raster.

Example (PowerShell)::

    pixi run python src/scripts/inspect_coherence.py `
      --input data/processed/interferogram_30m/interferogram_deburst.dim `
      --output data/processed/coherence_analysis

The source product is in radar geometry; the .img file may not be georeferenced.
"""

from __future__ import annotations

import argparse
import json
import logging
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning

LOG = logging.getLogger(__name__)


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


def inspect_coherence(
    input_dim: Path,
    output_dir: Path,
    *,
    histogram_bins: int = 10000,
) -> CoherenceReport:
    """Calculate coherence statistics in blocks; approximate median via histogram.

    Values are assumed to be unscaled coherence in [0, 1]. The median is
    estimated from a histogram with ``histogram_bins`` equal-width bins.
    """
    input_dim = Path(input_dim).resolve()
    if input_dim.suffix.lower() != ".dim" or not input_dim.is_file():
        raise FileNotFoundError(f"SNAP .dim product not found: {input_dim}")
    data_dir = input_dim.with_suffix(".data")
    if not data_dir.is_dir():
        raise FileNotFoundError(f"SNAP .data directory not found: {data_dir}")
    candidates = sorted(data_dir.glob("coh_*.img"))
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one coherence .img band in {data_dir}, "
            f"found {len(candidates)}: {[p.name for p in candidates]}"
        )
    if histogram_bins < 2:
        raise ValueError("histogram_bins must be >= 2")

    coherence_file = candidates[0]
    histogram = np.zeros(histogram_bins, dtype=np.int64)
    total_pixels = 0
    valid_pixels = 0
    value_sum = 0.0
    minimum = float("inf")
    maximum = float("-inf")
    count_03 = count_06 = count_08 = 0

    # Radar-geometry ENVI bands often have no affine geotransform. That is
    # expected and does not affect coherence statistics.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
        with rasterio.open(coherence_file) as src:
            if src.count != 1:
                raise ValueError(f"Expected one raster band, found {src.count}")
            for _, window in src.block_windows(1):
                block = src.read(1, window=window, masked=True)
                total_pixels += block.size
                values = block.compressed()
                values = values[np.isfinite(values)]
                values = values[(values >= 0) & (values <= 1)]
                if values.size == 0:
                    continue
                valid_pixels += int(values.size)
                value_sum += float(np.sum(values, dtype=np.float64))
                minimum = min(minimum, float(values.min()))
                maximum = max(maximum, float(values.max()))
                count_03 += int(np.count_nonzero(values > 0.3))
                count_06 += int(np.count_nonzero(values > 0.6))
                count_08 += int(np.count_nonzero(values > 0.8))
                histogram += np.histogram(values, bins=histogram_bins, range=(0, 1))[0]

    if valid_pixels == 0:
        raise ValueError(f"No valid coherence pixels in {coherence_file}")

    # Median estimate from a 0..1 histogram (error typically <= bin width).
    cumulative = np.cumsum(histogram)
    middle_left = (valid_pixels - 1) // 2
    middle_right = valid_pixels // 2
    left_idx = int(np.searchsorted(cumulative, middle_left + 1))
    right_idx = int(np.searchsorted(cumulative, middle_right + 1))
    median = ((left_idx + 0.5) + (right_idx + 0.5)) / (2 * histogram_bins)

    report = CoherenceReport(
        input_dim=str(input_dim),
        coherence_band=str(coherence_file),
        total_pixels=total_pixels,
        valid_pixels=valid_pixels,
        excluded_pixels=total_pixels - valid_pixels,
        mean=value_sum / valid_pixels,
        median=median,
        minimum=minimum,
        maximum=maximum,
        percent_above_03=100 * count_03 / valid_pixels,
        percent_above_06=100 * count_06 / valid_pixels,
        percent_above_08=100 * count_08 / valid_pixels,
        median_approximate=True,
        percent_excluded=100 * (total_pixels - valid_pixels) / total_pixels,
    )
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "coherence_statistics.json"
    report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    LOG.info("Mean coherence:   %.4f", report.mean)
    LOG.info("Median coherence: %.4f (histogram approximation)", report.median)
    LOG.info("Minimum:          %.4f", report.minimum)
    LOG.info("Maximum:          %.4f", report.maximum)
    LOG.info("Pixels > 0.3:     %.2f%%", report.percent_above_03)
    LOG.info("Pixels > 0.6:     %.2f%%", report.percent_above_06)
    LOG.info("Pixels > 0.8:     %.2f%%", report.percent_above_08)
    LOG.info(
        "Valid pixels:     %d / %d (excluded %.2f%%)",
        report.valid_pixels,
        report.total_pixels,
        report.percent_excluded,
    )
    LOG.info("Saved coherence report: %s", report_path)
    return report


def inspect_coherence_pipeline(
    interferogram_dim: Path,
    output_dir: Path,
    logger: logging.Logger,
    *,
    histogram_bins: int = 10000,
) -> CoherenceReport:
    """Tracked application entry point for coherence statistics."""
    logger.info("Inspecting coherence in %s", interferogram_dim)
    return inspect_coherence(
        input_dim=Path(interferogram_dim),
        output_dir=Path(output_dir),
        histogram_bins=histogram_bins,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="Debursted SNAP .dim product"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/processed/coherence_analysis")
    )
    parser.add_argument("--histogram-bins", type=int, default=10000)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    inspect_coherence(args.input, args.output, histogram_bins=args.histogram_bins)


if __name__ == "__main__":
    main()
