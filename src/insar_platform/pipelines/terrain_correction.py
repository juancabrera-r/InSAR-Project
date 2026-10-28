"""Range-Doppler terrain correction of a SNAP/SNAPHU BEAM-DIMAP product.

Example::

    from insar_platform.pipelines.terrain_correction import run_terrain_correction

    result = run_terrain_correction(
        input_product="data/processed/phase_unwrapping/unwrapped_interferogram.dim",
        output_directory="data/processed/terrain_correction",
        gpt="C:/Program Files/esa-snap/bin/gpt.exe",
        map_projection="EPSG:32628",
        pixel_spacing_m=10.0,
    )

The output remains in map coordinates; this module does not convert phase to
LOS displacement, correct atmospheric delay, or establish a phase reference.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import rasterio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TerrainCorrectionResult:
    input_product: Path
    output_product: Path
    graph_file: Path
    width: int
    height: int
    crs: str
    band_names: tuple[str, ...]


def _run_command(command: list[str]) -> None:
    logger.info("Executing: %s", subprocess.list2cmdline(command))
    started = time.monotonic()

    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    ) as process:
        assert process.stdout is not None

        for line in process.stdout:
            if line.strip():
                logger.info("[SNAP] %s", line.rstrip())

        returncode = process.wait()

    if returncode != 0:
        raise RuntimeError(
            f"SNAP GPT failed with exit code {returncode}: "
            f"{subprocess.list2cmdline(command)}"
        )

    logger.info("SNAP GPT finished in %.1f s", time.monotonic() - started)


def _dimap_band_names(product: Path) -> tuple[str, ...]:
    root = ET.parse(product).getroot()
    names: list[str] = []

    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "Spectral_Band_Info":
            continue

        for child in node:
            if child.tag.rsplit("}", 1)[-1] == "BAND_NAME" and child.text:
                names.append(child.text.strip())
                break

    return tuple(names)


def _validate_output(product: Path) -> tuple[int, int, str, tuple[str, ...]]:
    if not product.is_file():
        raise FileNotFoundError(f"Terrain-corrected DIMAP missing: {product}")

    data_dir = product.with_suffix(".data")

    if not data_dir.is_dir():
        raise FileNotFoundError(f"Terrain-corrected DIMAP data missing: {data_dir}")

    bands = _dimap_band_names(product)

    if not any(name.lower().replace("_", "").startswith("unwphase") for name in bands):
        raise RuntimeError(
            f"Terrain-corrected product has no unwrapped phase band: {bands}"
        )

    images = sorted(data_dir.rglob("*.img"))

    if not images:
        raise RuntimeError(f"No physical ENVI rasters in {data_dir}")

    unwrapped_images = [
        p for p in images if p.stem.lower().replace("_", "").startswith("unwphase")
    ]

    if len(unwrapped_images) != 1:
        raise RuntimeError(
            f"Expected one physical unwrapped phase raster; found {unwrapped_images}"
        )

    with rasterio.open(unwrapped_images[0]) as ds:
        if ds.count != 1 or ds.width < 1 or ds.height < 1:
            raise RuntimeError(f"Invalid output raster: {unwrapped_images[0]}")

        if ds.crs is None:
            raise RuntimeError(
                "Terrain-corrected raster has no CRS. "
                "Check SNAP mapProjection and DEM settings."
            )

        if ds.transform.is_identity:
            raise RuntimeError("Terrain-corrected raster has an identity geotransform")

        return ds.width, ds.height, ds.crs.to_string(), bands


def _write_graph(
    *,
    source: Path,
    target: Path,
    graph_file: Path,
    dem_name: str,
    map_projection: str,
    pixel_spacing_m: float,
    img_resampling_method: str,
    dem_resampling_method: str,
    nodata_value: float,
    save_dem: bool,
    save_incidence_angle: bool,
    save_lat_lon: bool,
) -> None:
    graph = ET.Element("graph", id="TerrainCorrectionGraph")
    ET.SubElement(graph, "version").text = "1.0"

    read = ET.SubElement(graph, "node", id="Read")
    ET.SubElement(read, "operator").text = "Read"
    params = ET.SubElement(read, "parameters")
    ET.SubElement(params, "file").text = str(source)

    correction = ET.SubElement(graph, "node", id="TerrainCorrection")
    ET.SubElement(correction, "operator").text = "Terrain-Correction"

    sources = ET.SubElement(correction, "sources")
    ET.SubElement(sources, "sourceProduct", refid="Read")

    params = ET.SubElement(correction, "parameters")

    values = {
        "demName": dem_name,
        "demResamplingMethod": dem_resampling_method,
        "imgResamplingMethod": img_resampling_method,
        "pixelSpacingInMeter": str(pixel_spacing_m),
        "mapProjection": map_projection,
        "nodataValueAtSea": "false",
        "saveDEM": str(save_dem).lower(),
        "saveLocalIncidenceAngle": str(save_incidence_angle).lower(),
        "saveLatLon": str(save_lat_lon).lower(),
        "saveSelectedSourceBand": "true",
    }
    logger.debug("saveDEM: %s", save_dem)

    for key, value in values.items():
        ET.SubElement(params, key).text = value

    write = ET.SubElement(graph, "node", id="Write")
    ET.SubElement(write, "operator").text = "Write"

    sources = ET.SubElement(write, "sources")
    ET.SubElement(sources, "sourceProduct", refid="TerrainCorrection")

    params = ET.SubElement(write, "parameters")
    ET.SubElement(params, "file").text = str(target)
    ET.SubElement(params, "formatName").text = "BEAM-DIMAP"

    ET.indent(graph, space="  ")
    ET.ElementTree(graph).write(
        graph_file,
        encoding="utf-8",
        xml_declaration=True,
    )


def run_terrain_correction(
    *,
    input_product: str | Path,
    output_directory: str | Path,
    gpt: str | Path,
    dem_name: str = "Copernicus 30m Global DEM",
    map_projection: str = "EPSG:32628",
    pixel_spacing_m: float = 10.0,
    img_resampling_method: str = "BILINEAR_INTERPOLATION",
    dem_resampling_method: str = "BILINEAR_INTERPOLATION",
    nodata_value: float = 0.0,
    save_dem: bool = False,
    save_incidence_angle: bool = True,
    save_lat_lon: bool = False,
    cache: str = "4G",
    threads: int = 2,
    reuse_existing: bool = False,
) -> TerrainCorrectionResult:
    """Geocode unwrapped Sentinel-1 phase with SNAP Range-Doppler correction.

    reuse_existing trusts a pre-existing output after structural validation;
    it does not verify that its settings or source match this invocation.
    """
    source = Path(input_product).resolve()
    target_dir = Path(output_directory).resolve()
    executable = Path(gpt).resolve()

    if not source.is_file():
        raise FileNotFoundError(source)

    if not source.with_suffix(".data").is_dir():
        raise FileNotFoundError(source.with_suffix(".data"))

    if not executable.is_file():
        raise FileNotFoundError(executable)

    if not map_projection.strip():
        raise ValueError("map_projection cannot be empty")

    if not dem_name.strip():
        raise ValueError("dem_name cannot be empty")

    if not math.isfinite(pixel_spacing_m) or pixel_spacing_m <= 0:
        raise ValueError("pixel_spacing_m must be positive and finite")

    if threads < 1:
        raise ValueError("threads must be >= 1")

    if not math.isfinite(nodata_value):
        raise ValueError("nodata_value must be finite")

    source_bands = _dimap_band_names(source)

    if not any(b.lower().replace("_", "").startswith("unwphase") for b in source_bands):
        raise RuntimeError(f"Input has no unwrapped phase band: {source_bands}")

    target_dir.mkdir(parents=True, exist_ok=True)

    output_product = target_dir / "terrain_corrected_interferogram.dim"
    graph_file = target_dir / "terrain_correction_graph.xml"

    if reuse_existing and output_product.is_file():
        width, height, crs, bands = _validate_output(output_product)

        logger.info(
            "Reusing terrain-corrected product: %s",
            output_product,
        )

        return TerrainCorrectionResult(
            source,
            output_product,
            graph_file,
            width,
            height,
            crs,
            bands,
        )

    _write_graph(
        source=source,
        target=output_product,
        graph_file=graph_file,
        dem_name=dem_name,
        map_projection=map_projection,
        pixel_spacing_m=pixel_spacing_m,
        img_resampling_method=img_resampling_method,
        dem_resampling_method=dem_resampling_method,
        nodata_value=nodata_value,
        save_dem=save_dem,
        save_incidence_angle=save_incidence_angle,
        save_lat_lon=save_lat_lon,
    )

    # Remove previous output to prevent stale bands.
    output_product.unlink(missing_ok=True)

    data_dir = output_product.with_suffix(".data")
    if data_dir.exists():
        shutil.rmtree(data_dir)

    _run_command(
        [
            str(executable),
            str(graph_file),
            "-c",
            cache,
            "-q",
            str(threads),
        ]
    )

    width, height, crs, bands = _validate_output(output_product)

    logger.info(
        "Terrain correction complete | Product: %s | Size: %dx%d | CRS: %s | Bands: %s",
        output_product,
        width,
        height,
        crs,
        bands,
    )

    return TerrainCorrectionResult(
        source,
        output_product,
        graph_file,
        width,
        height,
        crs,
        bands,
    )
