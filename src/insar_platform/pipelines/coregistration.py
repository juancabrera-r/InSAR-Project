"""SNAP GPT TOPS coregistration for a selected Sentinel-1 SLC acquisition pair.

Standalone PowerShell example::

    pixi run python src/insar_platform/pipelines/coregistration.py `
      --reference "data/raw/safe/REFERENCE.SAFE.zip" `
      --secondary "data/raw/safe/SECONDARY.SAFE.zip" `
      --bursts 3 4 5 `
      --gpt "C:/Program Files/esa-snap/bin/gpt.exe" `
      --output "data/processed/coregistration"

Burst indices are 1-based SNAP subswath indices, not CDSE burst IDs.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

if TYPE_CHECKING:
    from insar_platform.pipelines.acquisition import AcquisitionResult

LOG = logging.getLogger(__name__)


def node(
    graph: ET.Element,
    identifier: str,
    operator: str,
    sources: dict[str, str] | None = None,
    parameters: dict[str, object] | None = None,
) -> None:
    element = ET.SubElement(graph, "node", id=identifier)
    ET.SubElement(element, "operator").text = operator
    source_element = ET.SubElement(element, "sources")
    for name, refid in (sources or {}).items():
        ET.SubElement(source_element, name, refid=refid)
    params = ET.SubElement(
        element,
        "parameters",
        attrib={"class": "com.bc.ceres.binding.dom.XppDomElement"},
    )
    for name, value in (parameters or {}).items():
        ET.SubElement(params, name).text = (
            str(value).lower() if isinstance(value, bool) else str(value)
        )


def graph_file(
    path: Path,
    steps: list[tuple[str, str, dict[str, str], dict[str, object]]],
) -> Path:
    root = ET.Element("graph", id="Sentinel1TOPSCoregistration")
    ET.SubElement(root, "version").text = "1.0"
    for identifier, operator, sources, parameters in steps:
        node(root, identifier, operator, sources, parameters)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def check_safe(path: Path, *, verify_crc: bool = False) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"SAFE ZIP not found: {path}")
    try:
        with ZipFile(path) as archive:
            manifests = [
                name
                for name in archive.namelist()
                if name.endswith("/manifest.safe") or name == "manifest.safe"
            ]
            if len(manifests) != 1:
                raise ValueError(
                    f"{path}: expected exactly one SAFE manifest, found {len(manifests)}"
                )
            if verify_crc:
                bad_entry = archive.testzip()
                if bad_entry is not None:
                    raise ValueError(f"Corrupted SAFE ZIP entry: {bad_entry}")
    except BadZipFile as exc:
        raise ValueError(f"Invalid SAFE ZIP: {path}") from exc


def run_gpt(
    gpt: str,
    graph: Path,
    *,
    cache: str = "4G",
    threads: int = 2,
) -> None:
    if threads < 1:
        raise ValueError("threads must be >= 1")

    executable = shutil.which(os.environ.get("SNAP_GPT", gpt) if gpt == "gpt" else gpt)

    if executable is None:
        raise FileNotFoundError(
            f"SNAP GPT executable not found: {gpt!r}. "
            "Configure SNAP_GPT with the full path to gpt.exe."
        )

    command = [
        executable,
        str(graph.resolve()),
        "-c",
        cache,
        "-q",
        str(threads),
    ]

    LOG.info("Running SNAP: %s", command)
    subprocess.run(command, check=True)


def validate_dim(path: Path) -> None:
    data_dir = path.with_suffix(".data")
    if not path.is_file() or not data_dir.is_dir() or not any(data_dir.iterdir()):
        raise RuntimeError(f"SNAP output missing or incomplete: {path}")


def coregister(
    reference: Path,
    secondary: Path,
    output: Path,
    *,
    first_burst: int,
    last_burst: int,
    gpt: str = "gpt",
    dem_name: str = "Copernicus 30m Global DEM",
    cache: str = "4G",
    threads: int = 2,
    run_esd: bool = True,
    subswath: str = "IW3",
    polarization: str = "VV",
) -> Path:
    """Execute TOPSAR-Split, orbit correction, Back-Geocoding, and optional ESD."""
    reference = Path(reference).resolve()
    secondary = Path(secondary).resolve()
    output = Path(output).resolve()

    if reference == secondary:
        raise ValueError("Reference and secondary must be different SAFE products")
    if first_burst < 1 or last_burst < first_burst:
        raise ValueError("Invalid SNAP burst range (1-based, inclusive)")
    if run_esd and first_burst == last_burst:
        raise ValueError("ESD requires at least two bursts; use --no-esd for one burst")
    if threads < 1:
        raise ValueError("threads must be >= 1")
    if subswath not in {"IW1", "IW2", "IW3"}:
        raise ValueError(f"Unsupported IW subswath: {subswath}")
    if polarization not in {"VV", "VH", "HH", "HV"}:
        raise ValueError(f"Unsupported polarization: {polarization}")

    for archive in (reference, secondary):
        check_safe(archive)
    output.mkdir(parents=True, exist_ok=True)

    split_products: list[Path] = []
    for role, archive in (("reference", reference), ("secondary", secondary)):
        dim = output / f"{role}_{subswath.lower()}_{polarization.lower()}_orbit.dim"
        graph = graph_file(
            output / f"{role}_split_orbit.xml",
            [
                (
                    "Read",
                    "Read",
                    {},
                    {
                        "file": archive,
                        "formatName": "SENTINEL-1",
                    },
                ),
                (
                    "Split",
                    "TOPSAR-Split",
                    {"sourceProduct": "Read"},
                    {
                        "subswath": subswath,
                        "selectedPolarisations": polarization,
                        "firstBurstIndex": first_burst,
                        "lastBurstIndex": last_burst,
                    },
                ),
                (
                    "Orbit",
                    "Apply-Orbit-File",
                    {"sourceProduct": "Split"},
                    {
                        "orbitType": "Sentinel Precise (Auto Download)",
                        "polyDegree": 3,
                        "continueOnFail": False,
                    },
                ),
                (
                    "Write",
                    "Write",
                    {"sourceProduct": "Orbit"},
                    {
                        "file": dim,
                        "formatName": "BEAM-DIMAP",
                    },
                ),
            ],
        )
        run_gpt(gpt, graph, cache=cache, threads=threads)
        validate_dim(dim)
        split_products.append(dim)

    reference_dim, secondary_dim = split_products
    target = output / ("coregistered_esd.dim" if run_esd else "coregistered_bg.dim")
    steps: list[tuple[str, str, dict[str, str], dict[str, object]]] = [
        (
            "ReadReference",
            "Read",
            {},
            {
                "file": reference_dim,
                "formatName": "BEAM-DIMAP",
            },
        ),
        (
            "ReadSecondary",
            "Read",
            {},
            {
                "file": secondary_dim,
                "formatName": "BEAM-DIMAP",
            },
        ),
        (
            "BackGeocoding",
            "Back-Geocoding",
            {
                "sourceProduct.1": "ReadReference",
                "sourceProduct.2": "ReadSecondary",
            },
            {
                "demName": dem_name,
                "demResamplingMethod": "BILINEAR_INTERPOLATION",
                "resamplingType": "BISINC_5_POINT_INTERPOLATION",
                "maskOutAreaWithoutElevation": True,
                "outputRangeAzimuthOffset": False,
                "outputDerampDemodPhase": False,
            },
        ),
    ]
    source = "BackGeocoding"
    if run_esd:
        steps.append(
            (
                "ESD",
                "Enhanced-Spectral-Diversity",
                {"sourceProduct": "BackGeocoding"},
                {},
            )
        )
        source = "ESD"
    steps.append(
        (
            "Write",
            "Write",
            {"sourceProduct": source},
            {
                "file": target,
                "formatName": "BEAM-DIMAP",
            },
        )
    )
    graph = graph_file(output / "coregistration.xml", steps)
    run_gpt(gpt, graph, cache=cache, threads=threads)
    validate_dim(target)
    LOG.info("Coregistration completed: %s", target)
    return target


def resolve_safe_zip(acquisition: AcquisitionResult, raw_directory: Path) -> Path:
    """Locate the downloaded archive for one selected acquisition."""
    product_name = acquisition.product_name
    if not product_name.endswith(".SAFE"):
        product_name += ".SAFE"
    safe_zip = raw_directory / "safe" / f"{product_name}.zip"
    if not safe_zip.is_file():
        raise FileNotFoundError(f"Selected acquisition SAFE ZIP not found: {safe_zip}")
    return safe_zip


def coregistration_pipeline(
    acquisitions: list[AcquisitionResult],
    raw_directory: Path,
    output_dir: Path,
    logger: logging.Logger,
    *,
    first_burst: int,
    last_burst: int,
    gpt: str = "gpt",
    dem_name: str = "Copernicus 30m Global DEM",
    cache: str = "4G",
    threads: int = 2,
    run_esd: bool = True,
    subswath: str = "IW3",
    polarization: str = "VV",
    safe_inspection: object | None = None,
) -> Path:
    """Resolve selected acquisitions and run pairwise SNAP coregistration.

    Only the two selected acquisition objects are processed. Catalogue products
    not present in ``acquisitions`` are never opened.
    """
    if len(acquisitions) != 2:
        raise ValueError(
            f"Pairwise coregistration requires exactly 2 acquisitions; got {len(acquisitions)}"
        )
    if safe_inspection is not None:
        missing = getattr(safe_inspection, "total_missing", None)
        if missing is None:
            raise TypeError("safe_inspection must expose total_missing")
        if missing:
            raise RuntimeError(
                f"Cannot coregister: {missing} SAFE dependencies missing"
            )

    # The catalogue burst identifiers are not SNAP 1-based burst indices.
    # Keep the SNAP burst range explicit until metadata-based mapping is verified.
    if first_burst < 1 or last_burst < first_burst:
        raise ValueError("Invalid SNAP burst range")
    selected = sorted(acquisitions, key=lambda item: item.acquisition_time)
    reference = resolve_safe_zip(selected[0], Path(raw_directory))
    secondary = resolve_safe_zip(selected[1], Path(raw_directory))
    logger.info(
        "Coregistration pair | Reference: %s | Secondary: %s | Bursts: %d-%d",
        selected[0].product_name,
        selected[1].product_name,
        first_burst,
        last_burst,
    )
    return coregister(
        reference=reference,
        secondary=secondary,
        output=Path(output_dir),
        first_burst=first_burst,
        last_burst=last_burst,
        gpt=gpt,
        dem_name=dem_name,
        cache=cache,
        threads=threads,
        run_esd=run_esd,
        subswath=subswath,
        polarization=polarization,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--secondary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpt", default="gpt")
    parser.add_argument(
        "--bursts",
        type=int,
        nargs="+",
        required=True,
        help="1-based SNAP burst indices (must be consecutive)",
    )
    parser.add_argument("--dem-name", default="Copernicus 30m Global DEM")
    parser.add_argument("--cache", default="4G")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--subswath", choices=("IW1", "IW2", "IW3"), default="IW3")
    parser.add_argument(
        "--polarization", choices=("VV", "VH", "HH", "HV"), default="VV"
    )
    parser.add_argument("--no-esd", action="store_true")
    args = parser.parse_args()
    indices = sorted(set(args.bursts))
    if indices != list(range(indices[0], indices[-1] + 1)):
        parser.error("--bursts must be consecutive")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    coregister(
        reference=args.reference,
        secondary=args.secondary,
        output=args.output,
        first_burst=indices[0],
        last_burst=indices[-1],
        gpt=args.gpt,
        dem_name=args.dem_name,
        cache=args.cache,
        threads=args.threads,
        run_esd=not args.no_esd,
        subswath=args.subswath,
        polarization=args.polarization,
    )


if __name__ == "__main__":
    main()
