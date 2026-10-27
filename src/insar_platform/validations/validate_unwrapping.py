"""Block-wise validation of SNAP/SNAPHU phase unwrapping.

Reads physical ENVI rasters from SNAP BEAM-DIMAP products.

The wrapped interferometric phase is reconstructed from the
real and imaginary interferogram components:

    wrapped_phase = atan2(q_ifg, i_ifg)

This avoids attempting to open SNAP virtual phase bands
as physical ENVI raster files.
"""

from __future__ import annotations

import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

logger = logging.getLogger(__name__)

TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class PhaseUnwrappingValidationResult:
    width: int
    height: int
    valid_pixels: int
    valid_fraction: float
    circular_residual_mae_rad: float
    circular_residual_rmse_rad: float
    circular_residual_p95_rad: float
    residual_fraction_above_0_1_rad: float
    adjacent_jump_fraction_above_pi: float
    tile_boundary_jump_fraction_above_pi: float
    interior_jump_fraction_above_pi: float
    mean_coherence: float | None
    passed: bool


def _normalize_band_name(name: str) -> str:
    """Normalize SNAP band names for matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_band_file(
    product: Path,
    prefixes: tuple[str, ...],
) -> Path:
    """Find a physical ENVI raster in a SNAP DIMAP data directory."""
    product = Path(product).resolve()

    if not product.is_file():
        raise FileNotFoundError(product)

    data_directory = product.with_suffix(".data")

    if not data_directory.is_dir():
        raise FileNotFoundError(data_directory)

    matches: list[Path] = []

    for header in data_directory.rglob("*.hdr"):
        normalized = _normalize_band_name(header.stem)

        if not normalized.startswith(prefixes):
            continue

        raster_file = header.with_suffix(".img")

        if raster_file.is_file():
            matches.append(raster_file)

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one physical ENVI raster matching {prefixes} "
            f"in {data_directory}. Found: {matches}"
        )

    return matches[0]


def _interferogram_component_paths(product: Path) -> tuple[Path, Path]:
    """Select physical i/q bands referenced by the DIMAP virtual phase.

    Ignore unreferenced raster files, which may remain from earlier SNAP runs.
    """
    product = Path(product).resolve()
    if not product.is_file():
        raise FileNotFoundError(product)
    data_dir = product.with_suffix(".data")
    if not data_dir.is_dir():
        raise FileNotFoundError(data_dir)

    root = ET.parse(product).getroot()
    expressions: list[str] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in {"Virtual_Band_Info", "Spectral_Band_Info"}:
            continue
        fields = {
            child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in node
        }
        if fields.get("BAND_NAME", "").casefold().startswith("phase_ifg"):
            expression = fields.get("EXPRESSION", "")
            if expression:
                expressions.append(expression)

    if len(expressions) != 1:
        raise RuntimeError(
            f"Expected one virtual phase expression in {product}, "
            f"found {len(expressions)}: {expressions}"
        )

    match = re.fullmatch(
        r"\s*atan2\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*",
        expressions[0],
        flags=re.IGNORECASE,
    )
    if match is None:
        raise RuntimeError(f"Unsupported phase expression: {expressions[0]!r}")

    q_name, i_name = match.groups()
    if not _normalize_band_name(i_name).startswith(("iifg", "realifg")):
        raise RuntimeError(f"Unexpected real component in phase expression: {i_name}")
    if not _normalize_band_name(q_name).startswith(("qifg", "imagifg")):
        raise RuntimeError(
            f"Unexpected imaginary component in phase expression: {q_name}"
        )

    def resolve(name: str) -> Path:
        matches = [
            path
            for path in data_dir.rglob("*.img")
            if path.stem.casefold() == name.casefold()
            and path.with_suffix(".hdr").is_file()
            and "tie_point_grids"
            not in {part.casefold() for part in path.relative_to(data_dir).parts[:-1]}
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one physical raster for {name!r} "
                f"referenced by phase expression in {product}; found {matches}"
            )
        return matches[0]

    return resolve(i_name), resolve(q_name)


def _open_band(
    stack: ExitStack,
    product: Path,
    prefixes: tuple[str, ...] | None = None,
    *,
    raster_file: Path | None = None,
):
    """Open a physical SNAP band and register its cleanup."""
    if raster_file is None:
        if prefixes is None:
            raise ValueError("Provide prefixes or raster_file")
        raster_file = _find_band_file(product, prefixes)

    logger.info("Opening SNAP raster: %s", raster_file)

    dataset = stack.enter_context(rasterio.open(raster_file))

    if dataset.count != 1:
        raise RuntimeError(f"Expected one band in {raster_file}, found {dataset.count}")

    return dataset


def _valid_mask(
    values: np.ndarray,
    nodata: float | None,
) -> np.ndarray:
    """Return a mask of finite, non-NoData pixels."""
    mask = np.isfinite(values)

    if nodata is not None:
        if np.isnan(nodata):
            mask &= ~np.isnan(values)
        else:
            mask &= values != nodata

    return mask


def _circular_difference(
    unwrapped: np.ndarray,
    wrapped: np.ndarray,
) -> np.ndarray:
    """Compute phase residual mapped to [-pi, pi]."""
    difference = unwrapped.astype(np.float64) - wrapped.astype(np.float64)

    return np.arctan2(
        np.sin(difference),
        np.cos(difference),
    )


def _sample_boundaries(
    length: int,
    count: int,
) -> set[int]:
    """Estimate evenly distributed SNAPHU tile boundaries."""
    if count <= 1:
        return set()

    return {round(length * index / count) for index in range(1, count)}


def _accumulate_jumps(
    *,
    phase: np.ndarray,
    valid: np.ndarray,
    row_start: int,
    col_start: int,
    rows: int,
    cols: int,
    row_boundaries: set[int],
    col_boundaries: set[int],
) -> tuple[int, int, int, int, int, int]:
    """Count phase jumps between neighboring valid pixels."""
    adjacent_pairs = 0
    adjacent_jumps = 0

    boundary_pairs = 0
    boundary_jumps = 0

    interior_pairs = 0
    interior_jumps = 0

    # Horizontal neighbors.
    if phase.shape[1] > 1:
        delta = np.abs(
            np.diff(
                phase.astype(np.float64),
                axis=1,
            )
        )[:rows, :]

        pair_valid = valid[:rows, :-1] & valid[:rows, 1:]

        owned = np.arange(delta.shape[1]) < cols

        pair_valid &= owned[None, :]

        jumps = (delta > math.pi) & pair_valid

        global_cols = col_start + np.arange(delta.shape[1]) + 1

        boundary_mask = np.isin(
            global_cols,
            list(col_boundaries),
        )

        adjacent_pairs += int(pair_valid.sum())
        adjacent_jumps += int(jumps.sum())

        boundary_pairs += int(pair_valid[:, boundary_mask].sum())
        boundary_jumps += int(jumps[:, boundary_mask].sum())

        interior_pairs += int(pair_valid[:, ~boundary_mask].sum())
        interior_jumps += int(jumps[:, ~boundary_mask].sum())

    # Vertical neighbors.
    if phase.shape[0] > 1:
        delta = np.abs(
            np.diff(
                phase.astype(np.float64),
                axis=0,
            )
        )[:, :cols]

        pair_valid = valid[:-1, :cols] & valid[1:, :cols]

        owned = np.arange(delta.shape[0]) < rows

        pair_valid &= owned[:, None]

        jumps = (delta > math.pi) & pair_valid

        global_rows = row_start + np.arange(delta.shape[0]) + 1

        boundary_mask = np.isin(
            global_rows,
            list(row_boundaries),
        )

        adjacent_pairs += int(pair_valid.sum())
        adjacent_jumps += int(jumps.sum())

        boundary_pairs += int(pair_valid[boundary_mask, :].sum())
        boundary_jumps += int(jumps[boundary_mask, :].sum())

        interior_pairs += int(pair_valid[~boundary_mask, :].sum())
        interior_jumps += int(jumps[~boundary_mask, :].sum())

    return (
        adjacent_pairs,
        adjacent_jumps,
        boundary_pairs,
        boundary_jumps,
        interior_pairs,
        interior_jumps,
    )


def validate_phase_unwrapping(
    *,
    wrapped_product: str | Path,
    unwrapped_product: str | Path,
    output_json: str | Path | None = None,
    tile_rows: int = 4,
    tile_cols: int = 8,
    block_size: int = 512,
    residual_tolerance_rad: float = 0.1,
    maximum_bad_residual_fraction: float = 0.01,
) -> PhaseUnwrappingValidationResult:
    """Validate a SNAP/SNAPHU unwrapped interferogram.

    Performs:
        - Physical raster discovery
        - Wrapped phase reconstruction
        - Circular phase consistency
        - Residual error statistics
        - Adjacent phase discontinuity diagnostics
        - Approximate tile-boundary diagnostics
        - Coherence statistics
        - JSON report generation

    All raster operations are block-wise.
    """
    wrapped_product = Path(wrapped_product).resolve()
    unwrapped_product = Path(unwrapped_product).resolve()

    if block_size < 2:
        raise ValueError("block_size must be >= 2")

    if tile_rows < 1 or tile_cols < 1:
        raise ValueError("Tile counts must be positive")

    if not 0 < residual_tolerance_rad <= math.pi:
        raise ValueError("Residual tolerance must be in (0, pi]")

    if not 0 <= maximum_bad_residual_fraction <= 1:
        raise ValueError("Maximum bad residual fraction must be in [0, 1]")

    residual_count = 0
    residual_sum = 0.0
    residual_squared_sum = 0.0
    residual_bad_count = 0

    histogram = np.zeros(
        2048,
        dtype=np.int64,
    )

    valid_pixels = 0

    adjacent_pairs = 0
    adjacent_jumps = 0

    boundary_pairs = 0
    boundary_jumps = 0

    interior_pairs = 0
    interior_jumps = 0

    coherence_sum = 0.0
    coherence_count = 0

    with ExitStack() as stack:
        # Resolve the complex pair used by the virtual wrapped phase.
        real_path, imag_path = _interferogram_component_paths(wrapped_product)
        real_ds = _open_band(stack, wrapped_product, raster_file=real_path)
        imag_ds = _open_band(stack, wrapped_product, raster_file=imag_path)

        # Physical SNAPHU-unwrapped phase band.
        unwrapped_ds = _open_band(
            stack,
            unwrapped_product,
            ("unwphase", "unwrappedphase"),
        )

        # Coherence is optional for validation.
        try:
            coherence_ds = _open_band(
                stack,
                unwrapped_product,
                ("coh",),
            )
        except (FileNotFoundError, RuntimeError):
            logger.warning(
                "Coherence raster unavailable. Coherence statistics will be omitted."
            )
            coherence_ds = None

        width = real_ds.width
        height = real_ds.height

        datasets = [
            imag_ds,
            unwrapped_ds,
        ]

        if coherence_ds is not None:
            datasets.append(coherence_ds)

        for dataset in datasets:
            if dataset.width != width or dataset.height != height:
                raise ValueError("Inconsistent raster dimensions")

        total_pixels = width * height

        real_nodata = real_ds.nodata
        imag_nodata = imag_ds.nodata
        unwrapped_nodata = unwrapped_ds.nodata

        coherence_nodata = coherence_ds.nodata if coherence_ds is not None else None

        row_boundaries = _sample_boundaries(
            height,
            tile_rows,
        )

        col_boundaries = _sample_boundaries(
            width,
            tile_cols,
        )

        logger.info(
            "Validating phase unwrapping | "
            "Width: %d | Height: %d | "
            "Pixels: %d | Block size: %d",
            width,
            height,
            total_pixels,
            block_size,
        )

        for row_start in range(
            0,
            height,
            block_size,
        ):
            for col_start in range(
                0,
                width,
                block_size,
            ):
                rows = min(
                    block_size,
                    height - row_start,
                )

                cols = min(
                    block_size,
                    width - col_start,
                )

                read_rows = min(
                    rows + 1,
                    height - row_start,
                )

                read_cols = min(
                    cols + 1,
                    width - col_start,
                )

                window = Window(
                    col_off=col_start,
                    row_off=row_start,
                    width=read_cols,
                    height=read_rows,
                )

                real = real_ds.read(
                    1,
                    window=window,
                )

                imag = imag_ds.read(
                    1,
                    window=window,
                )

                unwrapped = unwrapped_ds.read(
                    1,
                    window=window,
                )

                # Reconstruct SNAP's virtual wrapped phase.
                wrapped = np.arctan2(
                    imag.astype(np.float64),
                    real.astype(np.float64),
                )

                valid = (
                    _valid_mask(real, real_nodata)
                    & _valid_mask(imag, imag_nodata)
                    & _valid_mask(
                        unwrapped,
                        unwrapped_nodata,
                    )
                )

                # Zero-magnitude complex values do not
                # provide a meaningful phase.
                valid &= (real != 0) | (imag != 0)

                core_valid = valid[:rows, :cols]

                valid_pixels += int(core_valid.sum())

                # Circular phase residual.
                residual = np.abs(
                    _circular_difference(
                        unwrapped[:rows, :cols],
                        wrapped[:rows, :cols],
                    )
                )

                values = residual[core_valid]

                if values.size:
                    residual_count += int(values.size)

                    residual_sum += float(values.sum())

                    residual_squared_sum += float(np.square(values).sum())

                    residual_bad_count += int(
                        np.count_nonzero(values > residual_tolerance_rad)
                    )

                    counts, _ = np.histogram(
                        values,
                        bins=2048,
                        range=(0.0, math.pi),
                    )

                    histogram += counts

                # Coherence statistics.
                if coherence_ds is not None:
                    coherence_window = Window(
                        col_off=col_start,
                        row_off=row_start,
                        width=cols,
                        height=rows,
                    )

                    coherence = coherence_ds.read(
                        1,
                        window=coherence_window,
                    )

                    coherence_valid = (
                        core_valid
                        & _valid_mask(
                            coherence,
                            coherence_nodata,
                        )
                        & (coherence >= 0)
                        & (coherence <= 1)
                    )

                    coherence_sum += float(coherence[coherence_valid].sum())

                    coherence_count += int(coherence_valid.sum())

                # Spatial phase discontinuities.
                (
                    block_adjacent_pairs,
                    block_adjacent_jumps,
                    block_boundary_pairs,
                    block_boundary_jumps,
                    block_interior_pairs,
                    block_interior_jumps,
                ) = _accumulate_jumps(
                    phase=unwrapped,
                    valid=valid,
                    row_start=row_start,
                    col_start=col_start,
                    rows=rows,
                    cols=cols,
                    row_boundaries=row_boundaries,
                    col_boundaries=col_boundaries,
                )

                adjacent_pairs += block_adjacent_pairs
                adjacent_jumps += block_adjacent_jumps

                boundary_pairs += block_boundary_pairs
                boundary_jumps += block_boundary_jumps

                interior_pairs += block_interior_pairs
                interior_jumps += block_interior_jumps

    if residual_count == 0:
        raise RuntimeError("No valid phase pixels available for validation")

    residual_mae = residual_sum / residual_count

    residual_rmse = math.sqrt(residual_squared_sum / residual_count)

    cumulative = np.cumsum(histogram)

    p95_bin = int(
        np.searchsorted(
            cumulative,
            math.ceil(residual_count * 0.95),
        )
    )

    residual_p95 = (p95_bin + 0.5) * math.pi / len(histogram)

    bad_fraction = residual_bad_count / residual_count

    result = PhaseUnwrappingValidationResult(
        width=width,
        height=height,
        valid_pixels=valid_pixels,
        valid_fraction=(valid_pixels / total_pixels),
        circular_residual_mae_rad=residual_mae,
        circular_residual_rmse_rad=residual_rmse,
        circular_residual_p95_rad=residual_p95,
        residual_fraction_above_0_1_rad=bad_fraction,
        adjacent_jump_fraction_above_pi=(
            adjacent_jumps / adjacent_pairs if adjacent_pairs else 0.0
        ),
        tile_boundary_jump_fraction_above_pi=(
            boundary_jumps / boundary_pairs if boundary_pairs else 0.0
        ),
        interior_jump_fraction_above_pi=(
            interior_jumps / interior_pairs if interior_pairs else 0.0
        ),
        mean_coherence=(coherence_sum / coherence_count if coherence_count else None),
        passed=(bad_fraction <= maximum_bad_residual_fraction),
    )

    logger.info(
        "Phase unwrapping validation | "
        "Valid pixels: %d | "
        "Valid fraction: %.4f | "
        "Residual MAE: %.6f rad | "
        "Residual RMSE: %.6f rad | "
        "Residual P95: %.6f rad | "
        "Bad residual fraction: %.4f%% | "
        "Adjacent jumps: %.4f%% | "
        "Tile-boundary jumps: %.4f%% | "
        "Interior jumps: %.4f%% | "
        "Mean coherence: %s | "
        "Passed: %s",
        result.valid_pixels,
        result.valid_fraction,
        result.circular_residual_mae_rad,
        result.circular_residual_rmse_rad,
        result.circular_residual_p95_rad,
        result.residual_fraction_above_0_1_rad * 100,
        result.adjacent_jump_fraction_above_pi * 100,
        result.tile_boundary_jump_fraction_above_pi * 100,
        result.interior_jump_fraction_above_pi * 100,
        (
            f"{result.mean_coherence:.4f}"
            if result.mean_coherence is not None
            else "N/A"
        ),
        result.passed,
    )

    if output_json is not None:
        output_json = Path(output_json)

        output_json.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_json.write_text(
            json.dumps(
                asdict(result),
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )

        logger.info(
            "Phase validation report saved: %s",
            output_json,
        )

    return result
