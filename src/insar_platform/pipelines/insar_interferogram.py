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

import argparse
import json
import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

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


def validate_dim(path: Path) -> None:
    data = path.with_suffix(".data")
    if not path.is_file() or not data.is_dir() or not any(data.iterdir()):
        raise RuntimeError(f"Missing or incomplete SNAP output: {path}")


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
    write_graph(graph, steps)
    run_graph(gpt, graph, cache, threads)
    validate_dim(ifg_dim)
    bands = inspect_dim_bands(ifg_dim)
    LOG.info("Interferogram bands: %s", bands)
    if not any("coh" in b.lower() for b in bands):
        raise RuntimeError(
            "No coherence band found in interferogram output; inspect SNAP graph and product"
        )

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
                    {
                        "file": str(deburst_dim),
                        "formatName": "BEAM-DIMAP",
                    },
                ),
            ],
        )
        run_graph(gpt, deburst_graph, cache, threads)
        validate_dim(deburst_dim)
        result = deburst_dim
        LOG.info("Debursted bands: %s", inspect_dim_bands(deburst_dim))

    summary = {
        "input": str(input_dim),
        "output": str(result),
        "interferogram": str(ifg_dim),
        "deburst": deburst,
        "coherence_window_meters": coherence_window_meters,
        "subtract_flat_earth": subtract_flat_earth,
        "subtract_topographic_phase": subtract_topographic_phase,
        "bands": inspect_dim_bands(result),
        "note": "Coherence existence is checked, but geometric/phase quality requires independent validation.",
    }
    (output_dir / "processing_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    LOG.info("Interferogram processing completed: %s", result)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="Coregistered ESD .dim"
    )
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    parser.add_argument("--gpt", default="gpt")
    parser.add_argument("--cache", default="4G")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--coherence-window-meters", type=int, default=30)
    parser.add_argument("--no-deburst", action="store_true")
    parser.add_argument("--subtract-topographic-phase", action="store_true")
    parser.add_argument("--dem-name", default="Copernicus 30m Global DEM")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    generate_interferogram(
        args.input,
        args.output,
        gpt=args.gpt,
        cache=args.cache,
        threads=args.threads,
        coherence_window_meters=args.coherence_window_meters,
        deburst=not args.no_deburst,
        subtract_topographic_phase=args.subtract_topographic_phase,
        dem_name=args.dem_name,
    )


if __name__ == "__main__":
    main()
