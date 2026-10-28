"""Block-wise planar LOS trend diagnostics. Does not correct atmospheric/orbital effects."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class LOSTrendDiagnosticsResult:
    trend_file: Path
    residual_file: Path
    report_file: Path
    valid_pixels: int
    sampled_pixels: int
    east_gradient_mm_per_km: float
    north_gradient_mm_per_km: float
    gradient_mm_per_km: float
    plane_fit_r2: float | None
    elevation_correlation: float | None


def _valid(a: np.ndarray, nodata: float | None) -> np.ndarray:
    mask = np.isfinite(a)
    if nodata is not None and np.isfinite(nodata):
        mask &= a != nodata
    return mask


def _same_grid(a: rasterio.io.DatasetReader, b: rasterio.io.DatasetReader) -> bool:
    return (
        a.width == b.width
        and a.height == b.height
        and a.crs == b.crs
        and a.transform.almost_equals(b.transform)
    )


def run_los_trend_diagnostics(
    *,
    displacement_file: str | Path,
    output_directory: str | Path,
    elevation_file: str | Path | None = None,
    sample_stride: int = 12,
    block_size: int = 512,
    nodata_value: float = -9999.0,
    overwrite: bool = False,
) -> LOSTrendDiagnosticsResult:
    """Fit a planar trend to sampled valid LOS pixels, then export trend and residual.

    CRS must use projected metre coordinates. Optional elevation must share the
    exact raster grid. Samples are deterministic but spatially correlated;
    reported R² and elevation correlation are descriptive, not significance tests.
    """
    source = Path(displacement_file).resolve()
    destination = Path(output_directory).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    trend_file = destination / "los_planar_trend.tif"
    residual_file = destination / "los_detrended_diagnostic.tif"
    report_file = destination / "los_trend_diagnostics.json"
    if sample_stride < 1 or block_size < 16 or block_size % 16:
        raise ValueError(
            "sample_stride must be >= 1; block_size must be divisible by 16 and >= 16"
        )
    if not np.isfinite(nodata_value):
        raise ValueError("nodata_value must be finite")
    if not source.is_file():
        raise FileNotFoundError(source)
    if not overwrite and any(
        p.exists() for p in (trend_file, residual_file, report_file)
    ):
        raise FileExistsError("Diagnostics outputs exist; use overwrite=True")

    from contextlib import ExitStack

    with ExitStack() as stack:
        ds = stack.enter_context(rasterio.open(source))
        if ds.count != 1 or ds.crs is None or not ds.crs.is_projected:
            raise ValueError("LOS input must be single-band in a projected CRS")
        units = ds.crs.linear_units_factor
        if units is None or not math.isclose(units[1], 1.0, rel_tol=1e-6):
            raise ValueError("Projected CRS must have metre units")
        elev = (
            stack.enter_context(rasterio.open(elevation_file))
            if elevation_file
            else None
        )
        if elev is not None and (elev.count != 1 or not _same_grid(ds, elev)):
            raise ValueError(
                "Elevation must be single-band and exactly aligned with LOS"
            )

        # Coordinates in km relative to raster centre improve numerical conditioning.
        cx, cy = ds.transform * (ds.width / 2, ds.height / 2)
        xtx = np.zeros((3, 3), dtype=np.float64)
        xty = np.zeros(3, dtype=np.float64)
        sample_n = 0
        valid_n = 0
        for _, window in ds.block_windows(1):
            arr = ds.read(1, window=window)
            valid_n += int(_valid(arr, ds.nodata).sum())
            rows = np.arange(int(window.row_off), int(window.row_off + window.height))
            cols = np.arange(int(window.col_off), int(window.col_off + window.width))
            rows = rows[rows % sample_stride == 0]
            cols = cols[cols % sample_stride == 0]
            if not len(rows) or not len(cols):
                continue
            rr, cc = np.meshgrid(rows, cols, indexing="ij")
            x, y = ds.transform * (cc + 0.5, rr + 0.5)
            rloc = rows - int(window.row_off)
            cloc = cols - int(window.col_off)
            z = arr[np.ix_(rloc, cloc)]
            good = _valid(z, ds.nodata)
            if not good.any():
                continue
            A = np.column_stack(
                (np.ones(int(good.sum())), (x[good] - cx) / 1000, (y[good] - cy) / 1000)
            )
            values = z[good].astype(np.float64)
            xtx += A.T @ A
            xty += A.T @ values
            sample_n += len(values)
        if sample_n < 10 or np.linalg.matrix_rank(xtx) != 3:
            raise ValueError(
                "Insufficient spatially distributed valid LOS samples for planar fit"
            )
        beta = np.linalg.solve(xtx, xty)
        LOG.info(
            "LOS trend fit | Samples: %d | East: %.4f mm/km | North: %.4f mm/km",
            sample_n,
            beta[1] * 1000,
            beta[2] * 1000,
        )

        profile = ds.profile.copy()
        profile.update(
            driver="GTiff",
            dtype="float32",
            count=1,
            nodata=nodata_value,
            tiled=True,
            blockxsize=block_size,
            blockysize=block_size,
            compress="deflate",
            predictor=3,
            BIGTIFF="IF_SAFER",
        )
        profile.pop("interleave", None)
        sse = sst = sy = sy2 = 0.0
        n = 0
        # Elevation/LOS correlation on the same sampled pixels, using stable moments.
        elev_stats = np.zeros(
            6, dtype=np.float64
        )  # n, sum_x, sum_y, sum_xx, sum_yy, sum_xy
        try:
            with (
                rasterio.open(trend_file, "w", **profile) as trend_ds,
                rasterio.open(residual_file, "w", **profile) as residual_ds,
            ):
                trend_ds.set_band_description(
                    1, "Fitted LOS plane (m); diagnostic only"
                )
                residual_ds.set_band_description(
                    1, "LOS minus fitted plane (m); diagnostic only"
                )
                for _, window in trend_ds.block_windows(1):
                    z = ds.read(1, window=window)
                    good = _valid(z, ds.nodata)
                    rows = np.arange(
                        int(window.row_off), int(window.row_off + window.height)
                    )
                    cols = np.arange(
                        int(window.col_off), int(window.col_off + window.width)
                    )
                    rr, cc = np.meshgrid(rows, cols, indexing="ij")
                    x, y = ds.transform * (cc + 0.5, rr + 0.5)
                    plane = (
                        beta[0] + beta[1] * (x - cx) / 1000 + beta[2] * (y - cy) / 1000
                    )
                    trend = np.full(z.shape, nodata_value, dtype=np.float32)
                    residual = trend.copy()
                    if good.any():
                        vals = z[good].astype(np.float64)
                        predicted = plane[good]
                        trend[good] = predicted.astype(np.float32)
                        residual[good] = (vals - predicted).astype(np.float32)
                        delta = vals - predicted
                        sse += float(delta @ delta)
                        sy += float(vals.sum())
                        sy2 += float(vals @ vals)
                        n += len(vals)
                    trend_ds.write(trend, 1, window=window)
                    residual_ds.write(residual, 1, window=window)
                    if elev is not None:
                        h = elev.read(1, window=window)
                        # Limit correlation sample size while retaining matched observations.
                        sampled = (rr % sample_stride == 0) & (cc % sample_stride == 0)
                        both = good & _valid(h, elev.nodata) & sampled
                        if both.any():
                            a = z[both].astype(np.float64)
                            b = h[both].astype(np.float64)
                            elev_stats += [
                                len(a),
                                a.sum(),
                                b.sum(),
                                a @ a,
                                b @ b,
                                a @ b,
                            ]
        except BaseException:
            trend_file.unlink(missing_ok=True)
            residual_file.unlink(missing_ok=True)
            raise
        sst = sy2 - sy * sy / n
        r2 = float(1 - sse / sst) if sst > 0 else None
        en, ex, ey, exx, eyy, exy = elev_stats
        cov = exy - ex * ey / en if en else 0
        vx = exx - ex * ex / en if en else 0
        vy = eyy - ey * ey / en if en else 0
        corr = float(cov / math.sqrt(vx * vy)) if vx > 0 and vy > 0 else None

    result = LOSTrendDiagnosticsResult(
        trend_file=trend_file,
        residual_file=residual_file,
        report_file=report_file,
        valid_pixels=valid_n,
        sampled_pixels=sample_n,
        east_gradient_mm_per_km=float(beta[1] * 1000),
        north_gradient_mm_per_km=float(beta[2] * 1000),
        gradient_mm_per_km=float(math.hypot(beta[1], beta[2]) * 1000),
        plane_fit_r2=r2,
        elevation_correlation=corr,
    )
    report = {
        k: str(v) if isinstance(v, Path) else v for k, v in asdict(result).items()
    }
    report["interpretation_warning"] = (
        "Detrended LOS is diagnostic only; planar subtraction can remove real deformation. "
        "Elevation correlation does not uniquely identify atmospheric effects."
    )
    report_file.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    LOG.info(
        "LOS diagnostics | Gradient %.4f mm/km | R² %.3f | Elevation r %s | %s",
        result.gradient_mm_per_km,
        r2 if r2 is not None else float("nan"),
        f"{corr:.3f}" if corr is not None else "N/A",
        report_file,
    )
    return result
