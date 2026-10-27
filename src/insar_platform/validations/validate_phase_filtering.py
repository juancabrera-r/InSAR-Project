"""Memory-bounded Goldstein filtering validation and spatial quality diagnostics.

Uses physical i/q rasters to reconstruct wrapped phase. Spatial statistics are
sampled and descriptive; coherence equality is checked over the entire raster.
"""

from __future__ import annotations

import json
import logging
import math
import re
import warnings
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

logger = logging.getLogger(__name__)


@dataclass
class PhaseFilteringValidationResult:
    input_product: str
    filtered_product: str
    status: str = "PASS"
    input_bands: list[str] = field(default_factory=list)
    filtered_bands: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _band_names(dim: Path) -> list[str]:
    root = ET.parse(dim).getroot()
    return list(
        dict.fromkeys(
            (e.text or "").strip()
            for e in root.iter()
            if _local(e.tag) == "BAND_NAME" and (e.text or "").strip()
        )
    )


def _raster_index(dim: Path) -> dict[str, Path]:
    """Index physical ENVI rasters by exact, case-insensitive band name."""
    data_dir = dim.with_suffix(".data")
    if not data_dir.is_dir():
        return {}
    index: dict[str, Path] = {}
    for path in sorted(data_dir.rglob("*.img")):
        if "tie_point_grids" in {
            part.lower() for part in path.relative_to(data_dir).parts[:-1]
        }:
            continue
        key = path.stem.casefold()
        if key in index:
            raise ValueError(
                f"Duplicate physical raster name {path.stem!r}: {index[key]} and {path}"
            )
        index[key] = path
    return index


def _physical_band(dim: Path, prefixes: tuple[str, ...]) -> Path | None:
    """Resolve a unique auxiliary raster, rejecting ambiguous matches."""
    index = _raster_index(dim)
    candidates = [
        path
        for name, path in index.items()
        if name.startswith(tuple(p.casefold() for p in prefixes))
    ]
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous raster bands for {prefixes}: {candidates}")
    return candidates[0] if candidates else None


def _complex_pair(dim: Path) -> tuple[Path, Path] | None:
    """Resolve the physical i/q pair referenced by SNAP's phase band."""

    root = ET.parse(dim).getroot()
    data_dir = dim.with_suffix(".data")

    phase_expressions = []

    for node in root.iter():
        if _local(node.tag) not in {
            "Spectral_Band_Info",
            "Virtual_Band_Info",
        }:
            continue

        fields = {_local(child.tag): (child.text or "").strip() for child in node}

        name = fields.get("BAND_NAME", "")
        expression = fields.get("EXPRESSION", "")

        if name.lower().startswith("phase_ifg") and expression:
            phase_expressions.append(expression)

    if len(phase_expressions) != 1:
        raise ValueError(
            f"Expected exactly one declared interferometric phase "
            f"expression in {dim}, found {len(phase_expressions)}"
        )

    expression = phase_expressions[0]

    match = re.fullmatch(
        r"\s*atan2\s*\(\s*"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*",
        expression,
        flags=re.IGNORECASE,
    )

    if match is None:
        raise ValueError(f"Unsupported phase expression in {dim}: {expression}")

    q_name, i_name = match.groups()

    if not i_name.lower().startswith(("i_ifg", "real_ifg")):
        raise ValueError(f"Unexpected real band: {i_name}")

    if not q_name.lower().startswith(("q_ifg", "imag_ifg")):
        raise ValueError(f"Unexpected imaginary band: {q_name}")

    declared_bands = set(_band_names(dim))

    if i_name not in declared_bands or q_name not in declared_bands:
        raise ValueError(
            f"Phase expression references undeclared bands: {i_name}, {q_name}"
        )

    def resolve(name: str) -> Path:
        candidates = [
            path
            for path in data_dir.rglob("*.img")
            if path.stem == name
            and "tie_point_grids"
            not in {part.lower() for part in path.relative_to(data_dir).parts[:-1]}
        ]

        if len(candidates) != 1:
            raise ValueError(
                f"Expected one physical raster for {name}, "
                f"found {len(candidates)}: {candidates}"
            )

        return candidates[0]

    return resolve(i_name), resolve(q_name)


def _read(ds, window: Window) -> tuple[np.ndarray, np.ndarray]:
    a = ds.read(1, window=window, masked=True)
    return np.asarray(a.data), (~np.ma.getmaskarray(a)) & np.isfinite(a.data)


def _phase(i: np.ndarray, q: np.ndarray) -> np.ndarray:
    return np.arctan2(q, i)


def _wrap(delta: np.ndarray) -> np.ndarray:
    return np.arctan2(np.sin(delta), np.cos(delta))


def _circular_variance(phase: np.ndarray, valid: np.ndarray, radius: int = 2):
    """Local circular variance 1-|mean(exp(i*phase))|, using box sums.

    Returns NaN where fewer than 70% of neighbors are valid. This metric can
    also be high at genuine fringes, so reductions are not proof of denoising.
    """
    from scipy.ndimage import uniform_filter

    size = 2 * radius + 1
    v = valid.astype(np.float32)
    c = uniform_filter(
        np.where(valid, np.cos(phase), 0).astype(np.float32),
        size=size,
        mode="constant",
        cval=0,
    )
    s = uniform_filter(
        np.where(valid, np.sin(phase), 0).astype(np.float32),
        size=size,
        mode="constant",
        cval=0,
    )
    n = uniform_filter(v, size=size, mode="constant", cval=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        result = 1 - np.hypot(c, s) / n
    result[n < 0.70] = np.nan
    return np.clip(result, 0, 1)


def _metadata(dim: Path) -> dict:
    root = ET.parse(dim).getroot()
    bands = []
    for node in root.iter():
        if _local(node.tag) not in {"Spectral_Band_Info", "Virtual_Band_Info"}:
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in node}
        if fields.get("BAND_NAME"):
            bands.append(
                {
                    "name": fields["BAND_NAME"],
                    "unit": fields.get("PHYSICAL_UNIT") or fields.get("UNIT"),
                    "virtual": _local(node.tag) == "Virtual_Band_Info",
                    "expression": fields.get("EXPRESSION"),
                }
            )
    names = _band_names(dim)
    phase = [b for b in bands if b["name"].lower().startswith("phase_ifg")]
    return {
        "bands": bands,
        "phase_band_metadata": phase,
        "phase_band_declared": any(n.lower().startswith("phase_ifg") for n in names),
        "has_abstracted_metadata": any(
            _local(n.tag) == "Abstracted_Metadata" for n in root.iter()
        ),
    }


def validate_phase_filtering(
    input_dim: Path,
    filtered_dim: Path,
    *,
    report_path: Path | None = None,
    block_rows: int = 128,
    min_retained_fraction: float = 0.95,
    raise_on_error: bool = True,
    spatial_samples: int = 12,
    spatial_size: int = 512,
    coherence_atol: float = 1e-6,
) -> PhaseFilteringValidationResult:
    """Validate complex data, full-raster coherence, and sampled spatial quality.

    Spatial metrics are diagnostic, not pass/fail criteria. SNAPHU readiness is
    preliminary until SNAP SnaphuExport and an actual unwrap are tested.
    """
    input_dim, filtered_dim = Path(input_dim).resolve(), Path(filtered_dim).resolve()
    if block_rows < 1 or spatial_samples < 1 or spatial_size < 5:
        raise ValueError(
            "block_rows, spatial_samples and spatial_size must be positive"
        )
    if not 0 <= min_retained_fraction <= 1 or coherence_atol < 0:
        raise ValueError("Invalid retention threshold or coherence tolerance")
    for dim in (input_dim, filtered_dim):
        if dim.suffix.lower() != ".dim" or not dim.is_file():
            raise FileNotFoundError(f"SNAP DIMAP product not found: {dim}")
    result = PhaseFilteringValidationResult(
        input_product=str(input_dim),
        filtered_product=str(filtered_dim),
        input_bands=_band_names(input_dim),
        filtered_bands=_band_names(filtered_dim),
    )
    pairs = (_complex_pair(input_dim), _complex_pair(filtered_dim))
    if any(p is None for p in pairs):
        result.errors.append("Physical i/q interferogram rasters missing")
    else:
        with warnings.catch_warnings(), ExitStack() as stack:
            warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)

            if len(pairs) != 2:
                raise ValueError("Expected exactly two raster pairs")

            input_pair, filtered_pair = pairs

            if input_pair is None or filtered_pair is None:
                raise ValueError("Input or filtered raster pair is missing")

            input_i, input_q = input_pair
            filtered_i, filtered_q = filtered_pair

            i0 = stack.enter_context(rasterio.open(input_i))
            q0 = stack.enter_context(rasterio.open(input_q))
            i1 = stack.enter_context(rasterio.open(filtered_i))
            q1 = stack.enter_context(rasterio.open(filtered_q))

            ds = (i0, q0, i1, q1)
            coh_paths = (
                _physical_band(input_dim, ("coh_", "coherence")),
                _physical_band(filtered_dim, ("coh_", "coherence")),
            )
            cohs = [
                stack.enter_context(rasterio.open(p)) if p else None for p in coh_paths
            ]
            if len({(d.height, d.width) for d in (*ds, *(c for c in cohs if c))}) != 1:
                result.errors.append("Raster dimensions differ")
            else:
                h, w = i0.height, i0.width
                total = h * w
                counts = {
                    "input_valid": 0,
                    "filtered_valid": 0,
                    "common": 0,
                    "large": 0,
                    "coh_common": 0,
                    "coh_changed": 0,
                }
                abs_sum = sin_sum = cos_sum = coh_abs = coh_sq = coh_max = 0.0
                for row in range(0, h, block_rows):
                    win = Window(
                        col_off=0,  # type: ignore
                        row_off=row,  # type: ignore
                        width=w,  # type: ignore
                        height=min(block_rows, h - row),  # type: ignore
                    )
                    (a, va), (b, vb), (c, vc), (d, vd) = [_read(x, win) for x in ds]
                    v0, v1 = va & vb, vc & vd
                    common = v0 & v1
                    counts["input_valid"] += int(v0.sum())
                    counts["filtered_valid"] += int(v1.sum())
                    n = int(common.sum())
                    counts["common"] += n
                    if n:
                        p0 = _phase(a[common], b[common])
                        p1 = _phase(c[common], d[common])
                        delta = _wrap(p1 - p0)
                        abs_sum += float(np.abs(delta).sum(dtype=np.float64))
                        sin_sum += float(np.sin(delta).sum(dtype=np.float64))
                        cos_sum += float(np.cos(delta).sum(dtype=np.float64))
                        counts["large"] += int((np.abs(delta) > math.pi / 2).sum())
                    if all(c is not None for c in cohs):
                        (x, vx), (y, vy) = [_read(z, win) for z in cohs]
                        v = vx & vy
                        counts["coh_common"] += int(v.sum())
                        if v.any():
                            diff = np.abs(
                                x[v].astype(np.float64) - y[v].astype(np.float64)
                            )
                            coh_abs += float(diff.sum())
                            coh_sq += float(np.square(diff).sum())
                            coh_max = max(coh_max, float(diff.max()))
                            counts["coh_changed"] += int((diff > coherence_atol).sum())
                n = counts["common"]
                retained = n / counts["input_valid"] if counts["input_valid"] else 0
                result.metrics = {
                    "total_pixels": total,
                    "input_valid_pixels": counts["input_valid"],
                    "filtered_valid_pixels": counts["filtered_valid"],
                    "common_valid_pixels": n,
                    "input_valid_fraction": counts["input_valid"] / total,
                    "filtered_valid_fraction": counts["filtered_valid"] / total,
                    "retained_input_valid_fraction": retained,
                    "mean_absolute_circular_phase_change_rad": abs_sum / n
                    if n
                    else None,
                    "circular_phase_change_resultant_length": math.hypot(
                        sin_sum, cos_sum
                    )
                    / n
                    if n
                    else None,
                    "fraction_phase_change_above_pi_over_2": counts["large"] / n
                    if n
                    else None,
                }
                if not counts["input_valid"] or not n:
                    result.errors.append("No valid input/common complex pixels")
                elif retained < min_retained_fraction:
                    result.errors.append(
                        f"Valid pixel retention {retained:.2%} below {min_retained_fraction:.2%}"
                    )
                if counts["filtered_valid"] > counts["input_valid"]:
                    result.warnings.append(
                        "Filtered product has more valid pixels than input"
                    )
                cn = counts["coh_common"]
                if all(c is not None for c in cohs) and cn:
                    result.metrics["coherence_preservation"] = {
                        "compared_pixels": cn,
                        "mean_absolute_difference": coh_abs / cn,
                        "rmse": math.sqrt(coh_sq / cn),
                        "max_absolute_difference": coh_max,
                        "fraction_changed_above_tolerance": counts["coh_changed"] / cn,
                        "tolerance": coherence_atol,
                        "unchanged_within_tolerance": counts["coh_changed"] == 0,
                    }
                    if counts["coh_changed"]:
                        result.warnings.append(
                            "Coherence values differ; inspect coherence_preservation"
                        )
                else:
                    result.warnings.append(
                        "Cannot compare coherence: missing or empty coherence rasters"
                    )

                # Deterministic, geographically distributed samples; bounded RAM.
                size = min(spatial_size, h, w)
                rng = np.random.default_rng(20250104)
                spatial = []
                for _ in range(spatial_samples):
                    r = int(rng.integers(0, h - size + 1))
                    col = int(rng.integers(0, w - size + 1))
                    win = Window(
                        col_off=col,  # type: ignore
                        row_off=r,  # type: ignore
                        width=size,  # type: ignore
                        height=size,  # type: ignore
                    )
                    (a, va), (b, vb), (c, vc), (d, vd) = [_read(x, win) for x in ds]
                    valid = va & vb & vc & vd
                    if not valid.any():
                        continue
                    p0, p1 = _phase(a, b), _phase(c, d)
                    v0, v1 = (
                        _circular_variance(p0, valid),
                        _circular_variance(p1, valid),
                    )
                    good = np.isfinite(v0) & np.isfinite(v1)
                    # Compare adjacent circular gradients only on shared valid pairs.
                    gradients = []
                    for axis in (0, 1):
                        sl0 = [slice(None), slice(None)]
                        sl1 = [slice(None), slice(None)]
                        sl0[axis] = slice(None, -1)
                        sl1[axis] = slice(1, None)
                        sl0, sl1 = tuple(sl0), tuple(sl1)
                        adjacent = valid[sl0] & valid[sl1]
                        if adjacent.any():
                            g0 = np.abs(_wrap(p0[sl1] - p0[sl0]))[adjacent]
                            g1 = np.abs(_wrap(p1[sl1] - p1[sl0]))[adjacent]
                            gradients.append(
                                (
                                    float(g0.sum()),
                                    float(g1.sum()),
                                    len(g0),
                                    int((g0 > math.pi / 2).sum()),
                                    int((g1 > math.pi / 2).sum()),
                                )
                            )
                    gn = sum(x[2] for x in gradients)
                    item = {
                        "row": r,
                        "col": col,
                        "size": size,
                        "valid_pixels": int(valid.sum()),
                        "variance_comparison_pixels": int(good.sum()),
                        "mean_local_circular_variance_before": float(v0[good].mean())
                        if good.any()
                        else None,
                        "mean_local_circular_variance_after": float(v1[good].mean())
                        if good.any()
                        else None,
                        "mean_absolute_neighbor_phase_difference_before_rad": sum(
                            x[0] for x in gradients
                        )
                        / gn
                        if gn
                        else None,
                        "mean_absolute_neighbor_phase_difference_after_rad": sum(
                            x[1] for x in gradients
                        )
                        / gn
                        if gn
                        else None,
                        "fraction_large_neighbor_jumps_before": sum(
                            x[3] for x in gradients
                        )
                        / gn
                        if gn
                        else None,
                        "fraction_large_neighbor_jumps_after": sum(
                            x[4] for x in gradients
                        )
                        / gn
                        if gn
                        else None,
                    }
                    if all(c is not None for c in cohs):
                        coh, mask = _read(cohs[0], win)
                        high = good & mask & (coh >= 0.6)
                        item["high_coherence_variance_before"] = (
                            float(v0[high].mean()) if high.any() else None
                        )
                        item["high_coherence_variance_after"] = (
                            float(v1[high].mean()) if high.any() else None
                        )
                    spatial.append(item)
                result.metrics["spatial_quality_samples"] = spatial
                result.metrics["spatial_quality_note"] = (
                    "Sampled circular variance and neighbor gradients are descriptive; "
                    "they cannot alone prove deformation preservation. Inspect phase maps "
                    "and compare against stable reference areas."
                )

    meta0, meta1 = _metadata(input_dim), _metadata(filtered_dim)
    phase_meta = meta1["phase_band_metadata"]
    result.metrics["snaphu_preflight"] = {
        "input": meta0,
        "filtered": meta1,
        "complex_bands_available": pairs[1] is not None,
        "coherence_band_declared": any(
            b.lower().startswith("coh_") for b in result.filtered_bands
        ),
        "phase_band_declared": meta1["phase_band_declared"],
        "phase_units": [b["unit"] for b in phase_meta],
        "metadata_present": meta1["has_abstracted_metadata"],
        "snaphu_export_tested": False,
        "note": "SNAP SnaphuExport must be run to confirm actual export compatibility.",
    }
    if not meta1["phase_band_declared"]:
        result.errors.append("Filtered product does not declare a wrapped phase band")
    if not meta1["has_abstracted_metadata"]:
        result.warnings.append(
            "SNAP Abstracted_Metadata not found; SNAPHU export may fail"
        )
    if not phase_meta:
        result.warnings.append(
            "Could not resolve filtered phase band unit/type from metadata"
        )
    elif not all(
        (b["unit"] or "").lower() in {"rad", "radian", "radians"} for b in phase_meta
    ):
        result.warnings.append("Filtered phase unit is not explicitly radians")
    result.status = "FAIL" if result.errors else "PASS"
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(result), indent=2, allow_nan=False), encoding="utf-8"
        )
    logger.debug(
        "Phase filtering validation | status=%s | metrics=%s",
        result.status,
        result.metrics,
    )
    for message in result.warnings:
        logger.warning("Phase filtering validation: %s", message)
    if result.errors and raise_on_error:
        raise RuntimeError(
            "Phase filtering validation failed: " + "; ".join(result.errors)
        )
    return result
