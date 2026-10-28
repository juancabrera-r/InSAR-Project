"""Convert terrain-corrected Sentinel-1 unwrapped phase to referenced LOS displacement.

The reference region MUST be chosen independently as stable ground. Output is relative
LOS displacement, not an atmosphere/orbit-corrected deformation estimate.

Example:
    result = run_los_displacement(
        input_product='data/processed/terrain_correction/terrain_corrected_interferogram.dim',
        output_directory='data/processed/los_displacement',
        reference_bbox=None,  # auto-select candidate; verify stability
        coherence_threshold=0.3,
    )
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.io import DatasetReader
from rasterio.windows import Window, from_bounds

LOG = logging.getLogger(__name__)

# Approximate Sentinel-1 C-band wavelength. For precision, pass wavelength_m
# computed from the radar frequency in the source product metadata.
DEFAULT_WAVELENGTH_M = 0.05546576
DEFAULT_NODATA = -9999.0


@dataclass(frozen=True)
class LOSDisplacementResult:
    input_product: Path
    output_product: Path
    report_file: Path
    reference_phase_rad: float
    reference_valid_pixels: int
    valid_pixels: int
    total_pixels: int
    valid_fraction: float
    mean_displacement_m: float
    min_displacement_m: float
    max_displacement_m: float
    crs: str
    width: int
    height: int


def _find_band(data_dir: Path, prefix: str) -> Path:
    matches = [
        p
        for p in data_dir.glob("*.img")
        if p.stem.lower().replace("_", "").startswith(prefix)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {prefix!r} raster in {data_dir}; "
            f"found: {[p.name for p in matches]}"
        )
    if not matches[0].with_suffix(".hdr").is_file():
        raise FileNotFoundError(matches[0].with_suffix(".hdr"))
    return matches[0]


def _valid(values: np.ndarray, nodata: float | None) -> np.ndarray:
    valid = np.isfinite(values)
    if nodata is not None and np.isfinite(nodata):
        valid &= values != nodata
    return valid


def _reference_window(
    ds: DatasetReader,
    *,
    reference_bbox: tuple[float, float, float, float] | None,
    reference_window: tuple[int, int, int, int] | None,
) -> Window:
    if (reference_bbox is None) == (reference_window is None):
        raise ValueError(
            "Specify exactly one reference_bbox (in raster CRS) or "
            "reference_window (col_off, row_off, width, height). "
            "Choose a known stable reference area."
        )
    if reference_bbox is not None:
        xmin, ymin, xmax, ymax = reference_bbox
        if not all(map(math.isfinite, reference_bbox)) or xmin >= xmax or ymin >= ymax:
            raise ValueError("Invalid reference_bbox")
        raw = from_bounds(xmin, ymin, xmax, ymax, transform=ds.transform)
        left = max(0, math.floor(raw.col_off))
        top = max(0, math.floor(raw.row_off))
        right = min(ds.width, math.ceil(raw.col_off + raw.width))
        bottom = min(ds.height, math.ceil(raw.row_off + raw.height))
    else:
        assert reference_window is not None
        col, row, width, height = reference_window
        if min(col, row) < 0 or width <= 0 or height <= 0:
            raise ValueError("Invalid reference_window")
        left, top = col, row
        right, bottom = min(ds.width, col + width), min(ds.height, row + height)
    if left >= right or top >= bottom:
        raise ValueError("Reference area does not intersect the raster")
    return Window(left, top, right - left, bottom - top)


def _compute_reference(
    phase: DatasetReader,
    coherence: DatasetReader,
    window: Window,
    coherence_threshold: float,
    max_reference_samples: int,
    min_reference_pixels: int,
) -> tuple[float, int]:
    # Read a bounded number of pixels, including for very large reference AOIs.
    area = int(window.width * window.height)
    stride = max(1, math.ceil(math.sqrt(area / max_reference_samples)))
    # Subsample using rasterio's decimated reads. Validity and coherence are
    # sampled on the same grid; no entire-scene arrays are allocated.
    out_height = max(1, math.ceil(window.height / stride))
    out_width = max(1, math.ceil(window.width / stride))
    shape = (out_height, out_width)
    p = phase.read(
        1, window=window, out_shape=shape, resampling=rasterio.enums.Resampling.nearest
    )
    c = coherence.read(
        1, window=window, out_shape=shape, resampling=rasterio.enums.Resampling.nearest
    )
    mask = (
        _valid(p, phase.nodata)
        & _valid(c, coherence.nodata)
        & (c >= coherence_threshold)
        & (c <= 1.0)
    )
    count = int(mask.sum())
    if count < min_reference_pixels:
        raise ValueError(
            f"Only {count} valid sampled reference pixels; need at least "
            f"{min_reference_pixels}. Select a larger stable region or review coherence."
        )
    # Median is robust to isolated outliers but NOT to spatial 2π errors.
    return float(np.median(p[mask].astype(np.float64))), count


def _auto_reference_window(
    phase: DatasetReader,
    coherence: DatasetReader,
    *,
    coherence_threshold: float,
    size_pixels: int = 50,
    min_valid_fraction: float = 0.7,
    min_mean_coherence: float = 0.7,
) -> Window:
    """Find a high-quality *candidate* reference window, not proven stable ground.

    Scans non-overlapping windows with bounded memory, first on a coarse grid
    and then, if needed, on all remaining windows. Scores high coherence,
    coverage and low local phase variation. No entire-scene arrays are read.
    """
    if size_pixels < 8:
        raise ValueError("size_pixels must be >= 8")
    if not (0 < min_valid_fraction <= 1):
        raise ValueError("min_valid_fraction must be in (0, 1]")
    if not (0 <= min_mean_coherence <= 1):
        raise ValueError("min_mean_coherence must be in [0, 1]")

    cols = list(range(0, phase.width, size_pixels))
    rows = list(range(0, phase.height, size_pixels))
    # Cap initial candidate count to about 2500; progressively relax sampling.
    stride = max(1, math.ceil(math.sqrt(len(cols) * len(rows) / 2500)))
    best: tuple[float, Window] | None = None

    def scan(step: int) -> None:
        nonlocal best
        for row in rows[::step]:
            for col in cols[::step]:
                width = min(size_pixels, phase.width - col)
                height = min(size_pixels, phase.height - row)
                if width < size_pixels // 2 or height < size_pixels // 2:
                    continue
                window = Window(col, row, width, height)
                p = phase.read(1, window=window)
                c = coherence.read(1, window=window)
                mask = (
                    _valid(p, phase.nodata)
                    & _valid(c, coherence.nodata)
                    & (c >= coherence_threshold)
                    & (c <= 1.0)
                )
                coverage = float(mask.mean())
                if coverage < min_valid_fraction:
                    continue
                values = p[mask].astype(np.float64)
                mean_coh = float(np.mean(c[mask]))
                if mean_coh < min_mean_coherence:
                    continue
                mad = float(np.median(np.abs(values - np.median(values))))
                # Penalize abrupt phase variations and missing pixels.
                score = mean_coh * coverage / (1.0 + mad)
                if best is None or score > best[0]:
                    best = (score, window)

    scan(stride)
    if best is None and stride > 1:
        scan(1)
    if best is None:
        raise ValueError(
            "No automatic reference candidate meets coherence and coverage "
            "requirements. Provide reference_bbox/reference_window for a "
            "verified stable area or review the input rasters."
        )
    LOG.warning(
        "Automatically selected reference candidate %s (score %.4f). "
        "High coherence does not prove ground stability.",
        best[1],
        best[0],
    )
    return best[1]


def run_los_displacement(
    *,
    input_product: str | Path,
    output_directory: str | Path,
    reference_window: tuple[int, int, int, int] | None = None,
    reference_bbox: tuple[float, float, float, float] | None = None,
    auto_reference_size_pixels: int = 50,
    coherence_threshold: float = 0.3,
    wavelength_m: float = DEFAULT_WAVELENGTH_M,
    phase_to_los_sign: int = -1,
    nodata_value: float = DEFAULT_NODATA,
    block_size: int = 512,
    max_reference_samples: int = 250_000,
    min_reference_pixels: int = 100,
    overwrite: bool = False,
) -> LOSDisplacementResult:
    """Create a GeoTIFF of LOS displacement relative to a stable reference.

    If neither reference_bbox nor reference_window is supplied, select an
    automatic high-coherence candidate. This does not establish ground
    stability. A manual reference should be independently verified.
    reference_bbox is in the input raster CRS. The LOS sign depends on the
    upstream phase convention.
    """
    product = Path(input_product).resolve()
    if not product.is_file() or product.suffix.lower() != ".dim":
        raise FileNotFoundError(f"Input BEAM-DIMAP .dim not found: {product}")
    data_dir = product.with_suffix(".data")
    if not data_dir.is_dir():
        raise FileNotFoundError(data_dir)
    if not (0.0 <= coherence_threshold <= 1.0):
        raise ValueError("coherence_threshold must be in [0, 1]")
    if not math.isfinite(wavelength_m) or wavelength_m <= 0:
        raise ValueError("wavelength_m must be positive and finite")
    if phase_to_los_sign not in (-1, 1):
        raise ValueError("phase_to_los_sign must be -1 or +1")
    if not math.isfinite(nodata_value):
        raise ValueError("nodata_value must be finite")
    if block_size < 16 or block_size % 16:
        raise ValueError("block_size must be >= 16 and divisible by 16")
    if max_reference_samples < 1 or min_reference_pixels < 1:
        raise ValueError("Reference sample limits must be positive")

    phase_file = _find_band(data_dir, "unwphase")
    coherence_file = _find_band(data_dir, "coh")
    output_dir = Path(output_directory).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "los_displacement.tif"
    report_file = output_dir / "los_displacement_validation.json"
    if output_file.exists() and not overwrite:
        raise FileExistsError(f"{output_file} exists; pass overwrite=True to replace")

    if reference_bbox is not None and reference_window is not None:
        raise ValueError("Provide only one of reference_bbox or reference_window")

    with rasterio.open(phase_file) as phase, rasterio.open(coherence_file) as coherence:
        if (phase.width, phase.height) != (coherence.width, coherence.height):
            raise ValueError("Phase and coherence dimensions differ")
        if phase.crs is None or phase.transform.is_identity:
            raise ValueError("Unwrapped phase is not georeferenced")
        if coherence.crs != phase.crs or not coherence.transform.almost_equals(
            phase.transform
        ):
            raise ValueError(
                "Phase and coherence grids differ; align before conversion"
            )

        automatic_reference = reference_bbox is None and reference_window is None
        if automatic_reference:
            ref_window = _auto_reference_window(
                phase,
                coherence,
                coherence_threshold=coherence_threshold,
                size_pixels=auto_reference_size_pixels,
            )
        else:
            ref_window = _reference_window(
                phase, reference_bbox=reference_bbox, reference_window=reference_window
            )
        LOG.info(
            "LOS reference window: %s | automatic: %s", ref_window, automatic_reference
        )
        reference_phase, reference_count = _compute_reference(
            phase,
            coherence,
            ref_window,
            coherence_threshold,
            max_reference_samples,
            min_reference_pixels,
        )
        LOG.info(
            "LOS reference phase: %.6f rad (%d sampled valid pixels)",
            reference_phase,
            reference_count,
        )
        profile: dict[str, Any] = phase.profile.copy()
        profile.update(
            driver="GTiff",
            count=1,
            dtype="float32",
            nodata=nodata_value,
            tiled=True,
            blockxsize=block_size,
            blockysize=block_size,
            compress="deflate",
            predictor=3,
            BIGTIFF="IF_SAFER",
        )
        profile.pop("interleave", None)
        factor = phase_to_los_sign * wavelength_m / (4.0 * math.pi)
        count = 0
        total_sum = 0.0
        minimum = math.inf
        maximum = -math.inf
        try:
            with rasterio.open(output_file, "w", **profile) as dst:
                dst.set_band_description(1, "Referenced LOS displacement (m)")
                dst.update_tags(
                    1,
                    units="m",
                    reference_phase_rad=reference_phase,
                    coherence_threshold=coherence_threshold,
                    wavelength_m=wavelength_m,
                    phase_to_los_sign=phase_to_los_sign,
                )
                for _, window in dst.block_windows(1):
                    p = phase.read(1, window=window)
                    c = coherence.read(1, window=window)
                    valid = (
                        _valid(p, phase.nodata)
                        & _valid(c, coherence.nodata)
                        & (c >= coherence_threshold)
                        & (c <= 1.0)
                    )
                    out = np.full(p.shape, nodata_value, dtype=np.float32)
                    if np.any(valid):
                        displacement = (
                            p[valid].astype(np.float64) - reference_phase
                        ) * factor
                        out[valid] = displacement.astype(np.float32)
                        count += displacement.size
                        total_sum += float(displacement.sum())
                        minimum = min(minimum, float(displacement.min()))
                        maximum = max(maximum, float(displacement.max()))
                    dst.write(out, 1, window=window)
        except BaseException:
            output_file.unlink(missing_ok=True)
            raise

        if count == 0:
            output_file.unlink(missing_ok=True)
            raise RuntimeError("No valid displacement pixels were produced")

        total = phase.width * phase.height
        phase_transform = phase.transform
        result = LOSDisplacementResult(
            input_product=product,
            output_product=output_file,
            report_file=report_file,
            reference_phase_rad=reference_phase,
            reference_valid_pixels=reference_count,
            valid_pixels=count,
            total_pixels=total,
            valid_fraction=count / total,
            mean_displacement_m=total_sum / count,
            min_displacement_m=minimum,
            max_displacement_m=maximum,
            crs=phase.crs.to_string(),
            width=phase.width,
            height=phase.height,
        )

    with rasterio.open(output_file) as check:
        if check.crs is None or check.count != 1 or check.nodata != nodata_value:
            raise RuntimeError("Output GeoTIFF validation failed")

    report = {
        **{k: str(v) if isinstance(v, Path) else v for k, v in asdict(result).items()},
        "reference_bbox": reference_bbox,
        "reference_window": [
            int(ref_window.col_off),
            int(ref_window.row_off),
            int(ref_window.width),
            int(ref_window.height),
        ],
        "reference_selection": "automatic_candidate"
        if automatic_reference
        else "manual",
        "reference_bounds": list(rasterio.windows.bounds(ref_window, phase_transform)),
        "coherence_threshold": coherence_threshold,
        "wavelength_m": wavelength_m,
        "phase_to_los_sign": phase_to_los_sign,
        "units": "m",
        "limitations": (
            "Automatic reference selection does not establish stable ground. "
            "Relative LOS only. Atmospheric/orbital phase, DEM residuals, "
            "and integer-cycle unwrapping errors are not corrected."
        ),
    }
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    LOG.info(
        "LOS displacement completed | %s | Valid: %.2f%% | Range: %.4f to %.4f m",
        output_file,
        result.valid_fraction * 100,
        minimum,
        maximum,
    )
    return result
