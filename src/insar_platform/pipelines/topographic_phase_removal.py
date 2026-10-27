"""Topographic phase removal using ESA SNAP GPT."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DEM = "Copernicus 30m Global DEM"


def _create_graph(
    input_product: Path,
    output_product: Path,
    dem_name: str,
    orbit_degree: int = 3,
    output_topo_phase_band: bool = True,
    output_elevation_band: bool = True,
) -> ET.ElementTree:
    """Build a SNAP GPT graph for topographic phase removal."""
    graph = ET.Element(
        "graph",
        id="TopographicPhaseRemoval",
    )

    ET.SubElement(graph, "version").text = "1.0"

    # Read input interferogram.
    read = ET.SubElement(
        graph,
        "node",
        id="Read",
    )

    ET.SubElement(read, "operator").text = "Read"
    ET.SubElement(read, "sources")

    read_parameters = ET.SubElement(
        read,
        "parameters",
    )

    ET.SubElement(
        read_parameters,
        "file",
    ).text = str(input_product)

    # Remove topographic phase.
    topo = ET.SubElement(
        graph,
        "node",
        id="TopoPhaseRemoval",
    )

    ET.SubElement(
        topo,
        "operator",
    ).text = "TopoPhaseRemoval"

    topo_sources = ET.SubElement(
        topo,
        "sources",
    )

    ET.SubElement(
        topo_sources,
        "sourceProduct",
        refid="Read",
    )

    parameters = ET.SubElement(
        topo,
        "parameters",
    )

    ET.SubElement(
        parameters,
        "demName",
    ).text = dem_name

    ET.SubElement(
        parameters,
        "orbitDegree",
    ).text = str(orbit_degree)

    ET.SubElement(
        parameters,
        "outputTopoPhaseBand",
    ).text = str(output_topo_phase_band).lower()

    ET.SubElement(
        parameters,
        "outputElevationBand",
    ).text = str(output_elevation_band).lower()

    ET.SubElement(
        parameters,
        "outputLatLonBands",
    ).text = "false"

    ET.SubElement(
        parameters,
        "tileExtensionPercent",
    ).text = "100"

    # Write corrected interferogram.
    write = ET.SubElement(
        graph,
        "node",
        id="Write",
    )

    ET.SubElement(
        write,
        "operator",
    ).text = "Write"

    write_sources = ET.SubElement(
        write,
        "sources",
    )

    ET.SubElement(
        write_sources,
        "sourceProduct",
        refid="TopoPhaseRemoval",
    )

    write_parameters = ET.SubElement(
        write,
        "parameters",
    )

    ET.SubElement(
        write_parameters,
        "file",
    ).text = str(output_product)

    ET.SubElement(
        write_parameters,
        "formatName",
    ).text = "BEAM-DIMAP"

    return ET.ElementTree(graph)


def run_topographic_phase_removal(
    *,
    input_product: str | Path,
    output_product: str | Path,
    gpt_executable: str | Path,
    dem_name: str = DEFAULT_DEM,
    orbit_degree: int = 3,
    output_topo_phase_band: bool = True,
    output_elevation_band: bool = True,
) -> Path:
    """Remove DEM-derived topographic phase from an interferogram.

    Args:
        input_product: Existing SNAP interferogram (.dim).
        output_product: Destination corrected interferogram (.dim).
        gpt_executable: Path to ESA SNAP gpt.exe.
        dem_name: DEM installed or accessible through SNAP.
        orbit_degree: Orbit interpolation polynomial degree.
        output_topo_phase_band: Preserve simulated topographic phase.
        output_elevation_band: Preserve DEM elevation band.

    Returns:
        Path to the corrected BEAM-DIMAP product.

    Raises:
        FileNotFoundError: Required input or GPT executable missing.
        ValueError: Invalid configuration.
        subprocess.CalledProcessError: SNAP execution failed.
        RuntimeError: SNAP output was not generated.
    """
    input_product = Path(input_product).resolve()
    output_product = Path(output_product).resolve()
    gpt_executable = Path(gpt_executable).resolve()

    if input_product.suffix.lower() != ".dim":
        raise ValueError("Input product must be a SNAP .dim file")

    if output_product.suffix.lower() != ".dim":
        raise ValueError("Output product must be a SNAP .dim file")

    if not input_product.is_file():
        raise FileNotFoundError(f"Input interferogram not found: {input_product}")

    if not gpt_executable.is_file():
        raise FileNotFoundError(f"SNAP GPT executable not found: {gpt_executable}")

    if not 2 <= orbit_degree <= 10:
        raise ValueError("orbit_degree must be between 2 and 10")

    if input_product == output_product:
        raise ValueError("Input and output products must be different")

    if output_product.exists():
        raise FileExistsError(f"Output product already exists: {output_product}")

    output_product.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    graph = _create_graph(
        input_product=input_product,
        output_product=output_product,
        dem_name=dem_name,
        orbit_degree=orbit_degree,
        output_topo_phase_band=output_topo_phase_band,
        output_elevation_band=output_elevation_band,
    )

    with tempfile.TemporaryDirectory(prefix="snap_topo_") as temporary_directory:
        graph_file = Path(temporary_directory) / "topographic_phase_removal.xml"

        graph.write(
            graph_file,
            encoding="utf-8",
            xml_declaration=True,
        )

        command = [
            str(gpt_executable),
            str(graph_file),
        ]

        logger.info(
            "Starting topographic phase removal | "
            "Input: %s | DEM: %s | Orbit degree: %d",
            input_product,
            dem_name,
            orbit_degree,
        )

        logger.debug(
            "SNAP command: %s",
            subprocess.list2cmdline(command),
        )

        subprocess.run(
            command,
            check=True,
        )

    output_data_directory = output_product.with_suffix(".data")

    if not output_product.is_file() or not output_data_directory.is_dir():
        raise RuntimeError(
            f"SNAP did not generate a complete BEAM-DIMAP output: {output_product}"
        )

    logger.info(
        "Topographic phase removal completed | Product: %s",
        output_product,
    )

    return output_product
