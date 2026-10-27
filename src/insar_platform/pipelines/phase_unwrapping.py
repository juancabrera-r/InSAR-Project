"""Automated SNAP/SNAPHU phase unwrapping for Sentinel-1 DIMAP products.

Stages: SnaphuExport -> SNAPHU -> SnaphuImport -> DIMAP validation.
No manual editing of SNAP-generated files is required.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhaseUnwrappingResult:
    export_directory: Path
    snaphu_config: Path
    unwrapped_header: Path
    unwrapped_product: Path


def _run_command(command: list[str], *, cwd: Path | None = None) -> None:
    logger.info("Executing: %s", subprocess.list2cmdline(command))
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        if line.strip():
            logger.info("[PROCESS] %s", line.rstrip())
    returncode = process.wait()
    if returncode:
        raise RuntimeError(
            f"Command failed (exit code {returncode}): {subprocess.list2cmdline(command)}"
        )
    logger.info("Command completed in %.1f s", time.monotonic() - started)


def _find_one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.rglob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {pattern!r} in {directory}; found {matches}")
    return matches[0]


def _read_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2:
            result[parts[0].upper()] = parts[1]
    return result


def _verify_config(
    path: Path,
    *,
    stat_cost_mode: str,
    init_method: str,
    tile_rows: int,
    tile_cols: int,
    row_overlap: int,
    col_overlap: int,
    processors: int,
    tile_cost_threshold: int,
) -> dict[str, str]:
    actual = _read_config(path)
    expected = {
        "STATCOSTMODE": stat_cost_mode,
        "INITMETHOD": init_method,
        "NTILEROW": str(tile_rows),
        "NTILECOL": str(tile_cols),
        "ROWOVRLP": str(row_overlap),
        "COLOVRLP": str(col_overlap),
        "NPROC": str(processors),
        "TILECOSTTHRESH": str(tile_cost_threshold),
        "INFILEFORMAT": "FLOAT_DATA",
        "OUTFILEFORMAT": "FLOAT_DATA",
    }
    mismatches = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if actual.get(key, "").upper() != value.upper()
    }
    if mismatches:
        raise RuntimeError(f"SNAPHU configuration mismatch: {mismatches}")
    return actual


def _snaphu_inputs(config_file: Path) -> tuple[str, int]:
    """Parse SNAP's documented command comment, without shell interpretation."""
    text = config_file.read_text(encoding="utf-8-sig")
    match = re.search(
        r"(?im)^\s*#\s*snaphu(?:\.exe)?\s+-f\s+snaphu\.conf\s+(\S+)\s+(\d+)\s*$",
        text,
    )
    if not match:
        raise RuntimeError(f"SNAP-generated SNAPHU command not found: {config_file}")
    name = match.group(1)
    if Path(name).name != name:
        raise ValueError(f"Unexpected SNAPHU input path in config: {name}")
    return name, int(match.group(2))


def _validate_float_raster(
    path: Path, width: int, height: int, *, block_rows: int = 128
) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    expected_size = width * height * 4
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise RuntimeError(
            f"Invalid SNAPHU output size: {actual_size} bytes; expected {expected_size}: {path}"
        )
    valid = 0
    minimum = math.inf
    maximum = -math.inf
    with path.open("rb") as stream:
        for row in range(0, height, block_rows):
            n = min(block_rows, height - row) * width
            array = np.fromfile(stream, dtype="<f4", count=n)
            if array.size != n:
                raise RuntimeError(f"Truncated SNAPHU output: {path}")
            finite = array[np.isfinite(array)]
            valid += int(finite.size)
            if finite.size:
                minimum = min(minimum, float(finite.min()))
                maximum = max(maximum, float(finite.max()))
    if valid == 0:
        raise RuntimeError("SNAPHU output contains no finite phase values")
    return {
        "width": width,
        "height": height,
        "total_pixels": width * height,
        "finite_pixels": valid,
        "finite_fraction": valid / (width * height),
        "minimum_rad": minimum,
        "maximum_rad": maximum,
        "bytes": actual_size,
    }


def _validate_dimap(path: Path) -> None:
    if not path.is_file() or not path.with_suffix(".data").is_dir():
        raise RuntimeError(f"Missing SNAP DIMAP product or data directory: {path}")
    root = ET.parse(path).getroot()
    names = [
        element.text.strip()
        for element in root.iter()
        if element.tag.split("}")[-1] == "BAND_NAME" and element.text
    ]
    if not any(name.lower().startswith("unwphase") for name in names):
        raise RuntimeError(f"SNAPHU-imported product lacks UnwPhase band: {names}")


def _create_import_graph(
    wrapped_product: Path,
    unwrapped_header: Path,
    target_product: Path,
    graph_file: Path,
    *,
    keep_wrapped: bool,
) -> None:
    graph = ET.Element("graph", id="SnaphuImportGraph")
    ET.SubElement(graph, "version").text = "1.0"
    for node_id, path in (
        ("ReadWrapped", wrapped_product),
        ("ReadUnwrapped", unwrapped_header),
    ):
        node = ET.SubElement(graph, "node", id=node_id)
        ET.SubElement(node, "operator").text = "Read"
        parameters = ET.SubElement(node, "parameters")
        ET.SubElement(parameters, "file").text = str(path)
    node = ET.SubElement(graph, "node", id="SnaphuImport")
    ET.SubElement(node, "operator").text = "SnaphuImport"
    sources = ET.SubElement(node, "sources")
    # SNAP's SnaphuImport operator declares an array source named sourceProducts.
    ET.SubElement(sources, "sourceProducts").text = "ReadWrapped,ReadUnwrapped"
    parameters = ET.SubElement(node, "parameters")
    ET.SubElement(parameters, "doNotKeepWrapped").text = str(not keep_wrapped).lower()
    node = ET.SubElement(graph, "node", id="Write")
    ET.SubElement(node, "operator").text = "Write"
    sources = ET.SubElement(node, "sources")
    ET.SubElement(sources, "sourceProduct", refid="SnaphuImport")
    parameters = ET.SubElement(node, "parameters")
    ET.SubElement(parameters, "file").text = str(target_product)
    ET.SubElement(parameters, "formatName").text = "BEAM-DIMAP"
    ET.indent(graph, space="  ")
    ET.ElementTree(graph).write(graph_file, encoding="utf-8", xml_declaration=True)


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def run_phase_unwrapping(
    *,
    filtered_product: str | Path,
    output_directory: str | Path,
    gpt: str | Path,
    snaphu_executable: str | Path,
    stat_cost_mode: str = "DEFO",
    init_method: str = "MST",
    tile_rows: int = 4,
    tile_cols: int = 8,
    row_overlap: int = 200,
    col_overlap: int = 200,
    processors: int = 4,
    tile_cost_threshold: int = 500,
    keep_wrapped: bool = True,
) -> PhaseUnwrappingResult:
    """Export, unwrap, validate and import a filtered Sentinel-1 interferogram."""
    filtered_product = Path(filtered_product).resolve()
    output_directory = Path(output_directory).resolve()
    gpt = Path(gpt).resolve()
    snaphu_executable = Path(snaphu_executable).resolve()
    for path in (filtered_product, gpt, snaphu_executable):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not filtered_product.with_suffix(".data").is_dir():
        raise FileNotFoundError(filtered_product.with_suffix(".data"))
    if stat_cost_mode not in {"TOPO", "DEFO", "SMOOTH", "NOSTATCOSTS"}:
        raise ValueError(f"Invalid SNAPHU cost mode: {stat_cost_mode}")
    if init_method not in {"MST", "MCF"}:
        raise ValueError(f"Invalid SNAPHU initialization: {init_method}")
    if (
        min(tile_rows, tile_cols, processors) < 1
        or min(row_overlap, col_overlap, tile_cost_threshold) < 0
    ):
        raise ValueError("Invalid SNAPHU tile/processor/overlap/cost settings")

    output_directory.mkdir(parents=True, exist_ok=True)
    export_directory = output_directory / "snaphu_export"
    config_file = export_directory / filtered_product.stem / "snaphu.conf"
    unwrapped_product = output_directory / "unwrapped_interferogram.dim"
    graph_file = output_directory / "snaphu_import_graph.xml"
    state_file = output_directory / "phase_unwrapping_state.json"
    summary_file = output_directory / "processing_summary.json"
    settings = {
        "filtered_product": _fingerprint(filtered_product),
        "gpt": str(gpt),
        "snaphu_executable": str(snaphu_executable),
        "stat_cost_mode": stat_cost_mode,
        "init_method": init_method,
        "tile_rows": tile_rows,
        "tile_cols": tile_cols,
        "row_overlap": row_overlap,
        "col_overlap": col_overlap,
        "processors": processors,
        "tile_cost_threshold": tile_cost_threshold,
        "keep_wrapped": keep_wrapped,
    }
    previous = {}
    if state_file.is_file():
        try:
            previous = json.loads(state_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            logger.warning("Ignoring unreadable phase-unwrapping state")
    reuse = previous.get("settings") == settings
    expected = {
        "stat_cost_mode": stat_cost_mode,
        "init_method": init_method,
        "tile_rows": tile_rows,
        "tile_cols": tile_cols,
        "row_overlap": row_overlap,
        "col_overlap": col_overlap,
        "processors": processors,
        "tile_cost_threshold": tile_cost_threshold,
    }

    if not reuse or not config_file.is_file():
        if export_directory.exists():
            shutil.rmtree(export_directory)
        export_directory.mkdir(parents=True)
        _run_command(
            [
                str(gpt),
                "SnaphuExport",
                f"-Ssource={filtered_product}",
                f"-PtargetFolder={export_directory}",
                f"-PstatCostMode={stat_cost_mode}",
                f"-PinitMethod={init_method}",
                f"-PnumberOfTileRows={tile_rows}",
                f"-PnumberOfTileCols={tile_cols}",
                f"-ProwOverlap={row_overlap}",
                f"-PcolOverlap={col_overlap}",
                f"-PnumberOfProcessors={processors}",
                f"-PtileCostThreshold={tile_cost_threshold}",
                "-c",
                "4G",
                "-q",
                "2",
            ]
        )
        config_file = _find_one(export_directory, "snaphu.conf")
        reuse = False
    config = _verify_config(config_file, **expected)
    input_name, width = _snaphu_inputs(config_file)
    input_path = config_file.parent / input_name
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if input_path.stat().st_size % (width * 4):
        raise RuntimeError("SNAPHU phase input size is not divisible by row width")
    height = input_path.stat().st_size // (width * 4)
    if height < 1:
        raise RuntimeError("SNAPHU input raster is empty")
    coherence = config.get("CORRFILE")
    if not coherence or not (config_file.parent / coherence).is_file():
        raise RuntimeError("SNAPHU coherence input missing")
    output_name = config.get("OUTFILE")
    if not output_name or Path(output_name).name != output_name:
        raise RuntimeError("Invalid SNAPHU OUTFILE setting")
    unwrapped_image = config_file.parent / output_name
    unwrapped_header = unwrapped_image.with_suffix(".hdr")
    if not unwrapped_header.is_file():
        raise FileNotFoundError(unwrapped_header)

    if not (
        reuse and previous.get("unwrapped_validated") and unwrapped_image.is_file()
    ):
        unwrapped_image.unlink(missing_ok=True)
        # SNAP writes the command to snaphu.conf; the binary is explicitly configured.
        _run_command(
            [str(snaphu_executable), "-f", config_file.name, input_name, str(width)],
            cwd=config_file.parent,
        )
    raster_metrics = _validate_float_raster(unwrapped_image, width, height)
    state_file.write_text(
        json.dumps({"settings": settings, "unwrapped_validated": True}, indent=2),
        encoding="utf-8",
    )

    if not (
        reuse
        and unwrapped_product.is_file()
        and unwrapped_product.with_suffix(".data").is_dir()
    ):
        # Remove only our generated import output; never touch the filtered source.
        unwrapped_product.unlink(missing_ok=True)
        if unwrapped_product.with_suffix(".data").exists():
            shutil.rmtree(unwrapped_product.with_suffix(".data"))
        _create_import_graph(
            filtered_product,
            unwrapped_header,
            unwrapped_product,
            graph_file,
            keep_wrapped=keep_wrapped,
        )
        _run_command([str(gpt), str(graph_file), "-c", "4G", "-q", "2"])
    _validate_dimap(unwrapped_product)
    summary_file.write_text(
        json.dumps(
            {
                "status": "PASS",
                "settings": settings,
                "snaphu_config": str(config_file),
                "snaphu_output": str(unwrapped_image),
                "unwrapped_product": str(unwrapped_product),
                "unwrapped_raster_validation": raster_metrics,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Phase unwrapping completed: %s", unwrapped_product)
    return PhaseUnwrappingResult(
        export_directory, config_file, unwrapped_header, unwrapped_product
    )
