"""Generate Sentinel-1 TOPS interferogram, coherence and debursted product with SNAP GPT.

Example (PowerShell):
pixi run python src/insar_platform/pipelines/insar_interferogram.py `
>>   --input "data/processed/coregistration/coregistered_esd.dim" `
>>   --output "data/processed/interferogram" `
>>   --gpt "C:/Program Files/esa-snap/bin/gpt.exe" `
>>   --cache 4G `
>>   --threads 2

Requires an already coregistered Sentinel-1 SLC stack (Back-Geocoding + ESD).
The output is NOT terrain corrected or unwrapped.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from insar_platform.validations.validate_dim import validate_dim
from insar_platform.validations.validate_interferogram import validate_interferogram

LOG = logging.getLogger(__name__)


def add_node(
    graph: ET.Element,
    identifier: str,
    operator: str,
    sources: dict[str, str],
    parameters: dict[str, object],
) -> None:
    element = ET.SubElement(graph, "node", id=identifier)
    ET.SubElement(element, "operator").text = operator
    source_element = ET.SubElement(element, "sources")
    for name, ref in sources.items():
        ET.SubElement(source_element, name, refid=ref)
    params = ET.SubElement(
        element, "parameters", {"class": "com.bc.ceres.binding.dom.XppDomElement"}
    )
    for key, value in parameters.items():
        ET.SubElement(params, key).text = (
            str(value).lower() if isinstance(value, bool) else str(value)
        )


def write_graph(path: Path, steps: list[tuple[str, str, dict, dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("graph", id="Sentinel1Interferogram")
    ET.SubElement(root, "version").text = "1.0"
    for identifier, operator, sources, parameters in steps:
        add_node(root, identifier, operator, sources, parameters)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def inspect_dim_bands(path: Path) -> list[str]:
    """Read band names from BEAM-DIMAP metadata without loading large rasters."""
    root = ET.parse(path).getroot()
    names = []
    for element in root.iter():
        if element.tag.split("}")[-1] == "BAND_NAME" and element.text:
            names.append(element.text.strip())
    return list(dict.fromkeys(names))


def run_graph(gpt: str, graph: Path, cache: str, threads: int) -> None:
    if threads < 1:
        raise ValueError("threads must be >= 1")
    resolved_gpt = os.environ.get("SNAP_GPT", gpt) if gpt == "gpt" else gpt
    executable = shutil.which(resolved_gpt)
    if executable is None:
        raise FileNotFoundError(
            f"SNAP GPT executable not found: {resolved_gpt!r}. "
            "Set SNAP_GPT to the full path to gpt.exe."
        )
    command = [executable, str(graph.resolve()), "-c", cache, "-q", str(threads)]
    LOG.info("Running SNAP: %s", command)
    subprocess.run(command, check=True)


def materialize_phase(
    source_dim: Path,
    output_dim: Path,
    *,
    gpt: str,
    cache: str,
    threads: int,
) -> None:
    """Use SNAP to write real, imaginary, coherence and phase as physical bands."""
    bands = inspect_dim_bands(source_dim)

    def required(prefix: str) -> str:
        matches = [name for name in bands if name.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(f"Expected one {prefix!r} band, found: {matches}")
        return matches[0]

    real = required("i_ifg_")
    imag = required("q_ifg_")
    coherence = required("coh_")
    # Do not materialize Phase_ifg before Goldstein filtering.
    phase = required("Phase_ifg_")

    graph = ET.Element("graph", id="MaterializeInterferogramPhase")
    ET.SubElement(graph, "version").text = "1.0"
    add_node(
        graph,
        "Read",
        "Read",
        {},
        {"file": str(source_dim), "formatName": "BEAM-DIMAP"},
    )

    node = ET.SubElement(graph, "node", id="BandMaths")
    ET.SubElement(node, "operator").text = "BandMaths"
    sources = ET.SubElement(node, "sources")
    ET.SubElement(sources, "sourceProduct", refid="Read")
    params = ET.SubElement(
        node,
        "parameters",
        {"class": "com.bc.ceres.binding.dom.XppDomElement"},
    )
    targets = ET.SubElement(params, "targetBands")
    band_definitions = {
        real: (real, "real"),
        imag: (imag, "imaginary"),
        coherence: (coherence, "coherence"),
        phase: (
            f"({real} == 0 && {imag} == 0) ? 0 : atan2({imag}, {real})",
            "rad",
        ),
    }

    for name, (expression, unit) in band_definitions.items():
        target = ET.SubElement(targets, "targetBand")
        ET.SubElement(target, "name").text = name
        ET.SubElement(target, "type").text = "float32"
        ET.SubElement(target, "expression").text = expression
        ET.SubElement(target, "unit").text = unit

    add_node(
        graph,
        "Write",
        "Write",
        {"sourceProduct": "BandMaths"},
        {"file": str(output_dim), "formatName": "BEAM-DIMAP"},
    )
    graph_path = output_dim.with_suffix(".xml")
    ET.indent(graph, space="  ")
    ET.ElementTree(graph).write(
        graph_path,
        encoding="utf-8",
        xml_declaration=True,
    )
    run_graph(gpt, graph_path, cache, threads)
    validate_dim(output_dim)

    # Do not accept a virtual phase band as a replacement for the required raster.
    data_dir = output_dim.with_suffix(".data")
    if not (data_dir / f"{phase}.img").is_file():
        raise FileNotFoundError(
            f"SNAP did not materialize physical phase band {phase!r} in {data_dir}"
        )


def generate_interferogram(
    input_dim: Path,
    output_dir: Path,
    *,
    gpt: str = "gpt",
    cache: str = "4G",
    threads: int = 2,
    coherence_window_meters: int = 30,
    subtract_flat_earth: bool = True,
    subtract_topographic_phase: bool = False,
    dem_name: str = "Copernicus 30m Global DEM",
    deburst: bool = True,
) -> Path:
    """Compute complex interferogram and coherence, then optionally TOPSAR-Deburst."""
    if coherence_window_meters < 1:
        raise ValueError("coherence_window_meters must be >= 1")
    if threads < 1:
        raise ValueError("threads must be >= 1")
    input_dim = input_dim.resolve()
    validate_dim(input_dim)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.resolve()
    ifg_dim = output_dir / "interferogram_bursts.dim"
    graph = output_dir / "interferogram.xml"
    steps = [
        ("Read", "Read", {}, {"file": str(input_dim), "formatName": "BEAM-DIMAP"}),
        (
            "Interferogram",
            "Interferogram",
            {"sourceProduct": "Read"},
            {
                "subtractFlatEarthPhase": subtract_flat_earth,
                "srpPolynomialDegree": 5,
                "srpNumberPoints": 501,
                "orbitDegree": 3,
                "includeCoherence": True,
                "cohWinSizeMeters": coherence_window_meters,
                "subtractTopographicPhase": subtract_topographic_phase,
                "demName": dem_name,
            },
        ),
        (
            "Write",
            "Write",
            {"sourceProduct": "Interferogram"},
            {
                "file": str(ifg_dim),
                "formatName": "BEAM-DIMAP",
            },
        ),
    ]
    # 1. Generate the standard SNAP interferogram.
    write_graph(graph, steps)
    run_graph(gpt, graph, cache, threads)
    validate_dim(ifg_dim)

    # 2. Deburst the original SNAP product, retaining its SAR metadata.
    result = ifg_dim
    if deburst:
        deburst_dim = output_dir / "interferogram_deburst.dim"
        deburst_graph = output_dir / "deburst.xml"
        write_graph(
            deburst_graph,
            [
                (
                    "Read",
                    "Read",
                    {},
                    {"file": str(ifg_dim), "formatName": "BEAM-DIMAP"},
                ),
                ("Deburst", "TOPSAR-Deburst", {"sourceProduct": "Read"}, {}),
                (
                    "Write",
                    "Write",
                    {"sourceProduct": "Deburst"},
                    {"file": str(deburst_dim), "formatName": "BEAM-DIMAP"},
                ),
            ],
        )
        run_graph(gpt, deburst_graph, cache, threads)
        validate_dim(deburst_dim)
        result = deburst_dim

    # 3. Materialize phase AND retain physical I/Q/coherence in one product.
    final_dim = output_dir / "interferogram_final.dim"
    materialize_phase(result, final_dim, gpt=gpt, cache=cache, threads=threads)
    result = final_dim

    # 4. Validate the actual final product, never an unused intermediate.
    final_validation = validate_interferogram(
        result,
        report_path=output_dir / "interferogram_validation.json",
    )
    LOG.info(
        "Interferogram validation | status=%s | coherence=%s",
        final_validation.status,
        final_validation.coherence.get("mean"),
    )

    # 5. Save processing summary
    summary = {
        "input": str(input_dim),
        "output": str(result),
        "interferogram": str(ifg_dim),
        "materialized_output": str(result),
        "deburst": deburst,
        "coherence_window_meters": coherence_window_meters,
        "subtract_flat_earth": subtract_flat_earth,
        "subtract_topographic_phase": subtract_topographic_phase,
        "bands": inspect_dim_bands(result),
        "validation": {
            "interferogram_status": final_validation.status,
            "final_status": final_validation.status,
            "coherence_mean": final_validation.coherence.get("mean"),
            "coherence_std": final_validation.coherence.get("std"),
            "coherence_valid_fraction": final_validation.coherence.get(
                "valid_fraction"
            ),
            "high_coherence_fraction": final_validation.coherence.get(
                "above_threshold_fraction"
            ),
            "warnings": final_validation.warnings,
            "errors": final_validation.errors,
        },
    }

    summary_path = output_dir / "processing_summary.json"

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    LOG.info(
        "Interferogram processing completed | output=%s | status=%s",
        result,
        final_validation.status,
    )

    return result


def interferogram_pipeline(
    coregistered_dim: Path,
    output_dir: Path,
    logger: logging.Logger,
    *,
    gpt: str = "gpt",
    cache: str = "4G",
    threads: int = 2,
    coherence_window_meters: int = 30,
    subtract_flat_earth: bool = True,
    subtract_topographic_phase: bool = False,
    dem_name: str = "Copernicus 30m Global DEM",
    deburst: bool = True,
) -> Path:
    """Tracked application entry point; SNAP details stay in this module."""
    logger.info("Generating interferogram from %s", coregistered_dim)
    return generate_interferogram(
        input_dim=Path(coregistered_dim),
        output_dir=Path(output_dir),
        gpt=gpt,
        cache=cache,
        threads=threads,
        coherence_window_meters=coherence_window_meters,
        subtract_flat_earth=subtract_flat_earth,
        subtract_topographic_phase=subtract_topographic_phase,
        dem_name=dem_name,
        deburst=deburst,
    )
