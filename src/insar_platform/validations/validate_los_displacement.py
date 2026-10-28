"""Block-wise quality diagnostics for geocoded Sentinel-1 LOS displacement.

This is a diagnostic tool, not a proof of geophysical deformation.

Example:
    from insar_platform.validations.validate_los_displacement import validate_los_displacement
    result = validate_los_displacement(
        displacement_file='data/processed/los_displacement/los_displacement.tif',
        terrain_corrected_product='data/processed/terrain_correction/terrain_corrected_interferogram.dim',
        report_file='data/processed/los_displacement/quality_report.json',
        coherence_threshold=0.3,
        wavelength_m=0.05546576,
        phase_to_los_sign=-1,
        reference_phase_rad=4.311608,  # Prefer exact value from LOS pipeline result.
    )
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import DatasetReader
from rasterio.windows import Window

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class LOSQualityResult:
    displacement_file: str
    report_file: str
    crs: str
    width: int
    height: int
    total_pixels: int
    valid_pixels: int
    valid_fraction: float
    mean_m: float
    std_m: float
    min_m: float
    max_m: float
    percentiles_m: dict[str, float]
    mean_coherence: float | None
    coherence_threshold: float | None
    coherence_eligible_pixels: int | None
    phase_valid_pixels: int | None
    coherence_valid_pixels: int | None
    los_mask_disagreement_pixels: int | None
    adjacent_pairs: int
    adjacent_jump_fraction: float | None
    adjacent_jump_threshold_m: float
    median_abs_adjacent_difference_m: float | None
    planar_gradient_m_per_km: float | None
    planar_gradient_east_m_per_km: float | None
    planar_gradient_north_m_per_km: float | None
    plane_fit_r2: float | None
    phase_los_residual_rmse_m: float | None
    phase_los_residual_max_abs_m: float | None
    elevation_correlation: float | None
    warnings: tuple[str, ...]


def _find_raster(product: Path, prefix: str) -> Path:
    folder = product.with_suffix(".data")
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    matches = [
        p
        for p in folder.glob("*.img")
        if p.stem.lower().replace("_", "").startswith(prefix)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {prefix} band in {folder}; found {[p.name for p in matches]}"
        )
    return matches[0]


def _valid(arr: np.ndarray, nodata: float | None) -> np.ndarray:
    ok = np.isfinite(arr)
    if nodata is not None and math.isfinite(nodata):
        ok &= arr != nodata
    return ok


def _check_grid(reference: DatasetReader, other: DatasetReader, name: str) -> None:
    if (reference.width, reference.height) != (other.width, other.height):
        raise ValueError(f"{name} dimensions differ from LOS")
    if reference.crs != other.crs or not reference.transform.almost_equals(
        other.transform
    ):
        raise ValueError(f"{name} grid differs from LOS; align rasters first")


def _iter_windows(width: int, height: int, block: int):
    for row in range(0, height, block):
        for col in range(0, width, block):
            yield Window(col, row, min(block, width - col), min(block, height - row))


def validate_los_displacement(
    *,
    displacement_file: str | Path,
    terrain_corrected_product: str | Path | None = None,
    report_file: str | Path | None = None,
    coherence_threshold: float = 0.3,
    wavelength_m: float = 0.05546576,
    phase_to_los_sign: int = -1,
    reference_phase_rad: float | None = None,
    adjacent_jump_threshold_m: float = 0.02,
    block_size: int = 512,
    sample_stride: int = 16,
    max_sample_points: int = 200_000,
    elevation_file: str | Path | None = None,
) -> LOSQualityResult:
    """Analyze a LOS raster without loading the whole scene into memory.

    Sampling is deterministic and regular, so percentiles, planar trends, and
    elevation correlation are approximate. Jump statistics use all eligible
    horizontal/vertical adjacent pairs, including block boundaries.

    If reference_phase_rad is supplied, compares observed LOS against the exact
    expected conversion of the geocoded phase. Otherwise this check is skipped.
    """
    if not 0 <= coherence_threshold <= 1:
        raise ValueError("coherence_threshold must be in [0,1]")
    if not math.isfinite(wavelength_m) or wavelength_m <= 0:
        raise ValueError("wavelength_m must be positive")
    if phase_to_los_sign not in (-1, 1):
        raise ValueError("phase_to_los_sign must be -1 or 1")
    if block_size < 16 or sample_stride < 1 or max_sample_points < 1:
        raise ValueError("Invalid block_size/sample_stride/max_sample_points")
    if not math.isfinite(adjacent_jump_threshold_m) or adjacent_jump_threshold_m <= 0:
        raise ValueError("adjacent_jump_threshold_m must be positive")

    from contextlib import ExitStack

    path = Path(displacement_file).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    report = (
        Path(report_file).resolve()
        if report_file
        else path.with_name("los_quality_report.json")
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    with ExitStack() as stack:
        los = stack.enter_context(rasterio.open(path))
        if los.count != 1 or los.crs is None or los.transform.is_identity:
            raise ValueError("LOS GeoTIFF must have one band and valid georeferencing")
        if not los.crs.is_projected:
            warnings.append(
                "CRS is not projected; plane gradient units may not be meters"
            )
        if los.crs.linear_units and los.crs.linear_units.lower() not in (
            "metre",
            "meter",
            "metres",
            "meters",
            "m",
        ):
            warnings.append(
                f"Projected CRS linear units are {los.crs.linear_units!r}, not meters"
            )

        phase = coh = elevation = None
        if terrain_corrected_product is not None:
            product = Path(terrain_corrected_product).resolve()
            phase = stack.enter_context(
                rasterio.open(_find_raster(product, "unwphase"))
            )
            coh = stack.enter_context(rasterio.open(_find_raster(product, "coh")))
            _check_grid(los, phase, "Unwrapped phase")
            _check_grid(los, coh, "Coherence")
        if elevation_file is not None:
            elevation = stack.enter_context(rasterio.open(elevation_file))
            _check_grid(los, elevation, "Elevation")
        if reference_phase_rad is not None and phase is None:
            raise ValueError(
                "terrain_corrected_product is required for phase-to-LOS check"
            )

        total = los.width * los.height
        n = 0
        summation = 0.0
        sumsq = 0.0
        minimum = math.inf
        maximum = -math.inf
        phase_count = coh_count = eligible_count = disagreement = 0
        coh_sum = coh_n = 0
        pairs = jumps = 0
        residual_sse = residual_n = 0
        residual_max = 0.0
        _: list[np.ndarray] = []
        sample_x: list[np.ndarray] = []
        sample_y: list[np.ndarray] = []
        sample_z: list[np.ndarray] = []
        sample_elev: list[np.ndarray] = []
        sample_n = 0
        factor = phase_to_los_sign * wavelength_m / (4 * math.pi)

        for window in _iter_windows(los.width, los.height, block_size):
            d = los.read(1, window=window)
            valid = _valid(d, los.nodata)
            values = d[valid].astype(np.float64)
            if values.size:
                n += values.size
                summation += float(values.sum())
                sumsq += float(np.dot(values, values))
                minimum = min(minimum, float(values.min()))
                maximum = max(maximum, float(values.max()))

            p = c = None
            if phase is not None and coh is not None:
                p = phase.read(1, window=window)
                c = coh.read(1, window=window)
                pv = _valid(p, phase.nodata)
                cv = _valid(c, coh.nodata) & (c >= 0) & (c <= 1)
                eligible = pv & cv & (c >= coherence_threshold)
                phase_count += int(pv.sum())
                coh_count += int(cv.sum())
                eligible_count += int(eligible.sum())
                disagreement += int(np.count_nonzero(valid != eligible))
                if np.any(valid & cv):
                    coh_sum += float(c[valid & cv].astype(np.float64).sum())
                    coh_n += int(np.count_nonzero(valid & cv))
                if reference_phase_rad is not None:
                    joint = valid & pv
                    if np.any(joint):
                        residual = (
                            d[joint].astype(np.float64)
                            - (p[joint].astype(np.float64) - reference_phase_rad)
                            * factor
                        )
                        residual_sse += float(np.dot(residual, residual))
                        residual_n += residual.size
                        residual_max = max(
                            residual_max, float(np.max(np.abs(residual)))
                        )

            # Include right and bottom halo pixels to count every neighboring pair once.
            extended = Window(
                window.col_off,
                window.row_off,
                min(window.width + 1, los.width - window.col_off),
                min(window.height + 1, los.height - window.row_off),
            )
            ext = los.read(1, window=extended)
            ext_valid = _valid(ext, los.nodata)
            h_valid = (
                ext_valid[: int(window.height), :-1]
                & ext_valid[: int(window.height), 1:]
            )
            h_valid = h_valid[:, : min(int(window.width), h_valid.shape[1])]
            h_diff = np.abs(
                np.diff(ext[: int(window.height), :], axis=1)[:, : h_valid.shape[1]]
            )
            v_valid = (
                ext_valid[:-1, : int(window.width)] & ext_valid[1:, : int(window.width)]
            )
            v_valid = v_valid[: min(int(window.height), v_valid.shape[0]), :]
            v_diff = np.abs(
                np.diff(ext[:, : int(window.width)], axis=0)[: v_valid.shape[0], :]
            )
            pairs += int(h_valid.sum() + v_valid.sum())
            jumps += int(np.count_nonzero(h_diff[h_valid] > adjacent_jump_threshold_m))
            jumps += int(np.count_nonzero(v_diff[v_valid] > adjacent_jump_threshold_m))

            # Regularly spaced, bounded sample for approximate percentiles/trend.
            if sample_n < max_sample_points:
                rr = np.arange(int(window.row_off), int(window.row_off + window.height))
                cc = np.arange(int(window.col_off), int(window.col_off + window.width))
                local_rows = np.flatnonzero(rr % sample_stride == 0)
                local_cols = np.flatnonzero(cc % sample_stride == 0)
                if local_rows.size and local_cols.size:
                    sub = np.ix_(local_rows, local_cols)
                    ok = valid[sub]
                    ys, xs = np.nonzero(ok)
                    available = max_sample_points - sample_n
                    if len(xs) > available:
                        xs, ys = xs[:available], ys[:available]
                    if len(xs):
                        rows = rr[local_rows][ys].astype(np.float64) + 0.5
                        cols = cc[local_cols][xs].astype(np.float64) + 0.5
                        tr = los.transform
                        east = tr.c + tr.a * cols + tr.b * rows
                        north = tr.f + tr.d * cols + tr.e * rows
                        sample_x.append(east)
                        sample_y.append(north)
                        sample_z.append(d[sub][ok][: len(xs)].astype(np.float64))
                        if elevation is not None:
                            z = elevation.read(1, window=window)[sub][ok][: len(xs)]
                            z = z.astype(np.float64)
                            z[~_valid(z, elevation.nodata)] = np.nan
                            sample_elev.append(z)
                        sample_n += len(xs)

        if n == 0:
            raise RuntimeError("LOS raster has no valid pixels")
        mean = summation / n
        std = math.sqrt(max(0.0, sumsq / n - mean**2))
        z = np.concatenate(sample_z) if sample_z else np.array([], dtype=np.float64)
        x = np.concatenate(sample_x) if sample_x else np.array([], dtype=np.float64)
        y = np.concatenate(sample_y) if sample_y else np.array([], dtype=np.float64)
        quantiles = {
            str(k): float(v)
            for k, v in zip(
                ("p01", "p05", "p25", "p50", "p75", "p95", "p99"),
                np.percentile(z, [1, 5, 25, 50, 75, 95, 99])
                if z.size
                else [math.nan] * 7,
            )
        }
        gx = gy = g = r2 = None
        if len(z) >= 100 and los.crs.is_projected:
            xc, yc = x.mean(), y.mean()
            A = np.column_stack((np.ones(len(z)), x - xc, y - yc))
            coefficients, _, rank, _ = np.linalg.lstsq(A, z, rcond=None)
            if rank == 3:
                gx, gy = float(coefficients[1] * 1000), float(coefficients[2] * 1000)
                g = float(math.hypot(gx, gy))
                fitted = A @ coefficients
                ss_tot = float(np.sum((z - z.mean()) ** 2))
                r2 = (
                    float(1 - np.sum((z - fitted) ** 2) / ss_tot)
                    if ss_tot > 0
                    else None
                )
        elevation_corr = None
        if sample_elev:
            elev = np.concatenate(sample_elev)
            ok = np.isfinite(elev)
            if ok.sum() >= 100 and np.std(elev[ok]) > 0 and np.std(z[ok]) > 0:
                elevation_corr = float(np.corrcoef(z[ok], elev[ok])[0, 1])
        if n / total < 0.6:
            warnings.append(
                "Less than 60% of raster pixels have valid LOS displacement"
            )
        if g is not None and abs(g) > 0.005:
            warnings.append(
                "Large fitted planar gradient (>5 mm/km); inspect orbit/atmosphere and real deformation"
            )
        if pairs and jumps / pairs > 0.01:
            warnings.append(
                "More than 1% of valid neighboring pixel pairs differ by >20 mm"
            )
        if disagreement:
            warnings.append(
                "LOS validity mask differs from phase/coherence eligibility mask"
            )
        if reference_phase_rad is None:
            warnings.append(
                "Phase-to-LOS consistency not checked: reference_phase_rad not provided"
            )
        warnings.append(
            "High coherence does not establish stable reference ground; inspect reference independently"
        )

        result = LOSQualityResult(
            displacement_file=str(path),
            report_file=str(report),
            crs=los.crs.to_string(),
            width=los.width,
            height=los.height,
            total_pixels=total,
            valid_pixels=n,
            valid_fraction=n / total,
            mean_m=mean,
            std_m=std,
            min_m=minimum,
            max_m=maximum,
            percentiles_m=quantiles,
            mean_coherence=coh_sum / coh_n if coh_n else None,
            coherence_threshold=coherence_threshold if coh is not None else None,
            coherence_eligible_pixels=eligible_count if coh is not None else None,
            phase_valid_pixels=phase_count if phase is not None else None,
            coherence_valid_pixels=coh_count if coh is not None else None,
            los_mask_disagreement_pixels=disagreement if coh is not None else None,
            adjacent_pairs=pairs,
            adjacent_jump_fraction=jumps / pairs if pairs else None,
            adjacent_jump_threshold_m=adjacent_jump_threshold_m,
            median_abs_adjacent_difference_m=None,
            planar_gradient_m_per_km=g,
            planar_gradient_east_m_per_km=gx,
            planar_gradient_north_m_per_km=gy,
            plane_fit_r2=r2,
            phase_los_residual_rmse_m=math.sqrt(residual_sse / residual_n)
            if residual_n
            else None,
            phase_los_residual_max_abs_m=residual_max if residual_n else None,
            elevation_correlation=elevation_corr,
            warnings=tuple(warnings),
        )
    report.write_text(
        json.dumps(asdict(result), indent=2, allow_nan=False), encoding="utf-8"
    )
    LOG.info(
        "LOS quality | Valid %.2f%% | Range %.3f..%.3f m | Trend %.3f mm/km | Report %s",
        result.valid_fraction * 100,
        result.min_m,
        result.max_m,
        result.planar_gradient_m_per_km * 1000
        if result.planar_gradient_m_per_km is not None
        else float("nan"),
        report,
    )
    for warning in warnings:
        LOG.warning("LOS quality: %s", warning)
    return result
