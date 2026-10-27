"""Scientific validation of SNAP Sentinel-1 interferogram products.

Checks:
- Required interferometric bands
- Coherence physical range and statistics
- Valid pixel coverage
- Wrapped phase range and circular statistics
- Phase consistency with complex interferogram (if available)

Designed for large BEAM-DIMAP products.
"""

from __future__ import annotations

import json
import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import DatasetReader
from rasterio.windows import Window

LOG = logging.getLogger(__name__)

TWO_PI = 2.0 * math.pi


@dataclass
class BandStatistics:
    name: str
    total_pixels: int = 0
    valid_pixels: int = 0
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    std: float | None = None


@dataclass
class InterferogramValidationResult:
    product: str
    status: str
    bands: list[str]

    coherence: dict = field(default_factory=dict)
    phase: dict = field(default_factory=dict)
    complex_consistency: dict = field(default_factory=dict)

    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_band_names(dim_path: Path) -> list[str]:
    root = ET.parse(dim_path).getroot()

    names = []

    for element in root.iter():
        if _local_name(element.tag) == "BAND_NAME" and element.text:
            names.append(element.text.strip())

    return list(dict.fromkeys(names))


def _find_band(
    bands: list[str],
    prefixes: tuple[str, ...],
) -> str | None:
    for band in bands:
        name = band.lower()

        if any(name.startswith(prefix) for prefix in prefixes):
            return band

    return None


def _find_raster(
    dim_path: Path,
    band_name: str,
) -> Path:
    """Locate the raster corresponding to a SNAP band name."""
    data_dir = dim_path.with_suffix(".data")

    # SNAP commonly uses band_name.img, but filename case may differ.
    candidates = list(data_dir.glob("*.img"))

    matches = [path for path in candidates if path.stem.lower() == band_name.lower()]

    if len(matches) == 1:
        return matches[0]

    if not matches:
        raise FileNotFoundError(
            f"Raster not found for band {band_name!r} in {data_dir}"
        )

    raise RuntimeError(f"Multiple raster files match band {band_name!r}: {matches}")


def _iter_windows(
    dataset: DatasetReader,
    block_size: int = 1024,
):
    """Iterate over raster windows without loading the full image."""
    for row in range(0, dataset.height, block_size):
        for col in range(0, dataset.width, block_size):
            yield Window(
                col_off=col,  # type: ignore
                row_off=row,  # type: ignore
                width=min(block_size, dataset.width - col),  # type: ignore
                height=min(block_size, dataset.height - row),  # type: ignore
            )


def _valid_data(
    dataset: DatasetReader,
    window: Window,
) -> tuple[np.ndarray, np.ndarray]:
    data = dataset.read(1, window=window, masked=True)

    values = np.asarray(data.data, dtype=np.float64)

    mask = ~np.ma.getmaskarray(data) & np.isfinite(values)

    return values, mask


def _statistics(
    raster_path: Path,
    *,
    block_size: int = 1024,
    valid_range: tuple[float, float] | None = None,
    threshold: float | None = None,
) -> dict:
    """Calculate streaming statistics and valid-range violations."""
    total = 0
    valid = 0
    count = 0

    value_sum = 0.0
    square_sum = 0.0

    minimum = math.inf
    maximum = -math.inf

    above_threshold = 0
    out_of_range = 0

    with rasterio.open(raster_path) as dataset:
        for window in _iter_windows(dataset, block_size):
            values, mask = _valid_data(dataset, window)

            total += values.size
            valid += int(mask.sum())

            if not mask.any():
                continue

            samples = values[mask]

            count += samples.size
            value_sum += float(samples.sum())
            square_sum += float(np.square(samples).sum())

            minimum = min(minimum, float(samples.min()))
            maximum = max(maximum, float(samples.max()))

            if threshold is not None:
                above_threshold += int(np.count_nonzero(samples >= threshold))

            if valid_range is not None:
                low, high = valid_range

                out_of_range += int(
                    np.count_nonzero((samples < low) | (samples > high))
                )

    if count == 0:
        return {
            "total_pixels": total,
            "valid_pixels": 0,
            "valid_fraction": 0.0,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "std": None,
            "above_threshold_fraction": None,
            "out_of_range_pixels": 0,
        }

    mean = value_sum / count
    variance = max(square_sum / count - mean**2, 0.0)

    return {
        "total_pixels": total,
        "valid_pixels": valid,
        "valid_fraction": valid / total if total else 0.0,
        "minimum": minimum,
        "maximum": maximum,
        "mean": mean,
        "std": math.sqrt(variance),
        "above_threshold_fraction": (
            above_threshold / count if threshold is not None else None
        ),
        "out_of_range_pixels": out_of_range,
    }


def _phase_statistics(
    raster_path: Path,
    *,
    block_size: int = 1024,
) -> dict:
    """Calculate circular phase statistics."""
    total = 0
    valid = 0

    sin_sum = 0.0
    cos_sum = 0.0

    minimum = math.inf
    maximum = -math.inf

    outside_wrapped_range = 0

    with rasterio.open(raster_path) as dataset:
        for window in _iter_windows(dataset, block_size):
            values, mask = _valid_data(dataset, window)

            total += values.size
            valid += int(mask.sum())

            if not mask.any():
                continue

            samples = values[mask]

            minimum = min(minimum, float(samples.min()))
            maximum = max(maximum, float(samples.max()))

            sin_sum += float(np.sin(samples).sum())
            cos_sum += float(np.cos(samples).sum())

            outside_wrapped_range += int(
                np.count_nonzero(
                    (samples < -math.pi - 1e-4) | (samples > math.pi + 1e-4)
                )
            )

    if valid == 0:
        return {
            "valid_fraction": 0.0,
            "minimum": None,
            "maximum": None,
            "circular_mean": None,
            "resultant_length": None,
            "outside_wrapped_range_pixels": 0,
        }

    circular_mean = math.atan2(sin_sum, cos_sum)

    resultant_length = math.hypot(sin_sum, cos_sum) / valid

    return {
        "valid_fraction": valid / total if total else 0.0,
        "minimum": minimum,
        "maximum": maximum,
        "circular_mean": circular_mean,
        "resultant_length": resultant_length,
        "outside_wrapped_range_pixels": outside_wrapped_range,
    }


def _complex_phase_consistency(
    real_path: Path,
    imag_path: Path,
    phase_path: Path,
    *,
    block_size: int = 1024,
    tolerance_rad: float = 0.1,
) -> dict:
    """Compare stored phase with atan2(imaginary, real)."""
    count = 0
    inconsistent = 0
    absolute_error_sum = 0.0

    with (
        rasterio.open(real_path) as real_ds,
        rasterio.open(imag_path) as imag_ds,
        rasterio.open(phase_path) as phase_ds,
    ):
        dimensions = {(ds.width, ds.height) for ds in (real_ds, imag_ds, phase_ds)}

        if len(dimensions) != 1:
            raise RuntimeError("Complex and phase bands have different dimensions")

        for window in _iter_windows(phase_ds, block_size):
            real, real_valid = _valid_data(real_ds, window)
            imag, imag_valid = _valid_data(imag_ds, window)
            phase, phase_valid = _valid_data(phase_ds, window)

            valid = real_valid & imag_valid & phase_valid

            if not valid.any():
                continue

            expected_phase = np.arctan2(
                imag[valid],
                real[valid],
            )

            residual = np.angle(np.exp(1j * (phase[valid] - expected_phase)))

            absolute_error = np.abs(residual)

            count += absolute_error.size
            inconsistent += int(np.count_nonzero(absolute_error > tolerance_rad))
            absolute_error_sum += float(absolute_error.sum())

    return {
        "compared_pixels": count,
        "mean_absolute_error_rad": (absolute_error_sum / count if count else None),
        "inconsistent_fraction": (inconsistent / count if count else None),
        "tolerance_rad": tolerance_rad,
    }


def validate_interferogram(
    product: Path,
    *,
    report_path: Path | None = None,
    coherence_threshold: float = 0.6,
    min_valid_fraction: float = 0.1,
    min_high_coherence_fraction: float = 0.1,
    phase_tolerance_rad: float = 0.1,
    block_size: int = 1024,
    raise_on_error: bool = True,
) -> InterferogramValidationResult:
    """Validate a SNAP interferogram and return a quality report."""
    product = Path(product).resolve()

    if not product.is_file():
        raise FileNotFoundError(product)

    if not 0 <= coherence_threshold <= 1:
        raise ValueError("coherence_threshold must be between 0 and 1")

    if not 0 <= min_valid_fraction <= 1:
        raise ValueError("min_valid_fraction must be between 0 and 1")

    if not 0 <= min_high_coherence_fraction <= 1:
        raise ValueError("min_high_coherence_fraction must be between 0 and 1")

    if block_size < 1:
        raise ValueError("block_size must be >= 1")

    bands = _read_band_names(product)

    result = InterferogramValidationResult(
        product=str(product),
        status="PASS",
        bands=bands,
    )

    coherence_band = _find_band(
        bands,
        ("coh_", "coherence"),
    )

    phase_band = _find_band(
        bands,
        ("phase_ifg", "phase_", "wrappedphase"),
    )

    real_band = _find_band(
        bands,
        ("i_ifg", "real_ifg"),
    )

    imag_band = _find_band(
        bands,
        ("q_ifg", "imag_ifg"),
    )

    if coherence_band is None:
        result.errors.append("Coherence band not found")

    if phase_band is None:
        result.errors.append("Wrapped phase band not found")

    if coherence_band is not None:
        coherence_path = _find_raster(
            product,
            coherence_band,
        )

        result.coherence = _statistics(
            coherence_path,
            block_size=block_size,
            valid_range=(-1e-5, 1.00001),
            threshold=coherence_threshold,
        )

        if result.coherence["valid_fraction"] < min_valid_fraction:
            result.errors.append("Insufficient valid coherence coverage")

        if result.coherence["out_of_range_pixels"] > 0:
            result.errors.append("Coherence contains values outside [0, 1]")

        high_fraction = result.coherence["above_threshold_fraction"]

        if high_fraction is not None and high_fraction < min_high_coherence_fraction:
            result.warnings.append("Low proportion of high-coherence pixels")

    if phase_band is not None:
        phase_path = _find_raster(
            product,
            phase_band,
        )

        result.phase = _phase_statistics(
            phase_path,
            block_size=block_size,
        )

        if result.phase["valid_fraction"] < min_valid_fraction:
            result.errors.append("Insufficient valid phase coverage")

        if result.phase["outside_wrapped_range_pixels"] > 0:
            result.errors.append("Phase contains values outside [-pi, pi]")

    if real_band is not None and imag_band is not None and phase_band is not None:
        result.complex_consistency = _complex_phase_consistency(
            _find_raster(product, real_band),
            _find_raster(product, imag_band),
            _find_raster(product, phase_band),
            block_size=block_size,
            tolerance_rad=phase_tolerance_rad,
        )

        inconsistent = result.complex_consistency["inconsistent_fraction"]

        if inconsistent is not None and inconsistent > 0.01:
            result.errors.append(
                "Complex interferogram and wrapped phase are inconsistent"
            )

    elif real_band is None or imag_band is None:
        result.warnings.append(
            "Complex interferogram components not available; "
            "phase consistency check skipped"
        )

    if result.errors:
        result.status = "FAIL"
    elif result.warnings:
        result.status = "WARNING"

    if report_path is not None:
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(asdict(result), indent=2),
            encoding="utf-8",
        )

    LOG.info(
        "Interferogram validation | status=%s | coherence_mean=%s",
        result.status,
        result.coherence.get("mean"),
    )

    for warning in result.warnings:
        LOG.warning("Interferogram: %s", warning)

    for error in result.errors:
        LOG.error("Interferogram: %s", error)

    if result.errors and raise_on_error:
        raise RuntimeError(
            "Interferogram validation failed: " + "; ".join(result.errors)
        )

    return result
