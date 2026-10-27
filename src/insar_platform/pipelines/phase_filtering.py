"""Goldstein phase filtering for SNAP Sentinel-1 interferograms."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from insar_platform.validations.validate_dim import validate_dim
from insar_platform.validations.validate_phase_filtering import (
    validate_phase_filtering,
)

logger = logging.getLogger(__name__)


def inspect_interferogram_bands(
    input_dim: Path,
) -> tuple[str, str, str]:
    """Find the complex interferogram and coherence bands."""

    root = ET.parse(input_dim).getroot()

    bands = []

    for element in root.iter():
        if element.tag.split("}")[-1] == "BAND_NAME" and element.text:
            bands.append(element.text.strip())

    bands = list(dict.fromkeys(bands))

    def required(prefix: str) -> str:
        matches = [name for name in bands if name.startswith(prefix)]

        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one {prefix!r} band in {input_dim}, found: {matches}"
            )

        return matches[0]

    real = required("i_ifg_")
    imag = required("q_ifg_")
    coherence = required("coh_")

    logger.info(
        "Goldstein input bands | real=%s | imaginary=%s | coherence=%s",
        real,
        imag,
        coherence,
    )

    return real, imag, coherence


def create_filter_graph(
    input_dim: Path,
    output_dim: Path,
    graph_path: Path,
    *,
    alpha: float = 1.0,
    fft_size: int = 64,
    window_size: int = 3,
    use_coherence_mask: bool = False,
) -> None:
    """Write a SNAP Goldstein filtering graph with band selection."""

    input_dim = Path(input_dim).resolve()
    output_dim = Path(output_dim).resolve()
    graph_path = Path(graph_path).resolve()

    real, imag, coherence = inspect_interferogram_bands(input_dim)

    graph = ET.Element(
        "graph",
        id="goldstein-phase-filtering",
    )
    ET.SubElement(graph, "version").text = "1.0"

    def add_node(
        identifier: str,
        operator: str,
        source: str | None,
        params: dict[str, object],
    ) -> None:
        node = ET.SubElement(graph, "node", id=identifier)
        ET.SubElement(node, "operator").text = operator

        if source is not None:
            sources = ET.SubElement(node, "sources")
            ET.SubElement(
                sources,
                "sourceProduct",
                refid=source,
            )

        parameters = ET.SubElement(
            node,
            "parameters",
            {"class": "com.bc.ceres.binding.dom.XppDomElement"},
        )

        for key, value in params.items():
            ET.SubElement(parameters, key).text = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )

    # 1. Read the original validated interferogram.
    add_node(
        "Read",
        "Read",
        None,
        {
            "file": str(input_dim),
            "formatName": "BEAM-DIMAP",
        },
    )

    # 2. Exclude the existing Phase_ifg band.
    # Keep the complex interferogram and coherence.
    add_node(
        "BandSelect",
        "BandSelect",
        "Read",
        {
            "sourceBands": f"{real},{imag},{coherence}",
        },
    )

    # 3. Apply Goldstein filtering to the selected bands.
    add_node(
        "Goldstein",
        "GoldsteinPhaseFiltering",
        "BandSelect",
        {
            "alpha": alpha,
            "FFTSizeString": fft_size,
            "windowSizeString": window_size,
            "useCoherenceMask": use_coherence_mask,
        },
    )

    # 4. Write the filtered interferogram.
    add_node(
        "Write",
        "Write",
        "Goldstein",
        {
            "file": str(output_dim),
            "formatName": "BEAM-DIMAP",
        },
    )

    ET.indent(graph, space="  ")

    graph_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    ET.ElementTree(graph).write(
        graph_path,
        encoding="utf-8",
        xml_declaration=True,
    )

    logger.info("Goldstein graph generated: %s", graph_path)


def phase_filtering_pipeline(
    interferogram_dim: Path,
    output_dir: Path,
    logger: logging.Logger,
    *,
    gpt: str,
    cache: str = "4G",
    threads: int = 2,
    alpha: float = 1.0,
    fft_size: int = 64,
    window_size: int = 3,
    use_coherence_mask: bool = False,
) -> Path:
    """Filter a validated interferogram using SNAP Goldstein filtering."""

    input_dim = Path(interferogram_dim).resolve()
    output_dir = Path(output_dir).resolve()

    validate_dim(input_dim)

    if not 0.0 <= alpha <= 1.0:
        raise ValueError("Goldstein alpha must be between 0 and 1")

    if fft_size < 2 or fft_size & (fft_size - 1):
        raise ValueError("FFT size must be a power of two >= 2")

    if window_size < 1:
        raise ValueError("Window size must be >= 1")

    if threads < 1:
        raise ValueError("SNAP threads must be >= 1")

    resolved_gpt = os.environ.get("SNAP_GPT", gpt) if gpt == "gpt" else gpt

    executable = shutil.which(resolved_gpt)

    if executable is None:
        raise FileNotFoundError(f"SNAP GPT executable not found: {resolved_gpt}")

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_dim = output_dir / "filtered_interferogram.dim"
    graph_path = output_dir / "goldstein_filter_graph.xml"

    create_filter_graph(
        input_dim,
        output_dim,
        graph_path,
        alpha=alpha,
        fft_size=fft_size,
        window_size=window_size,
        use_coherence_mask=use_coherence_mask,
    )

    command = [
        executable,
        str(graph_path),
        "-c",
        cache,
        "-q",
        str(threads),
    ]

    logger.info(
        "Goldstein phase filtering | alpha=%.2f | FFT=%d | window=%d",
        alpha,
        fft_size,
        window_size,
    )

    logger.info(
        "Executing SNAP GPT: %s",
        subprocess.list2cmdline(command),
    )

    # Capture the real SNAP error instead of returning
    # only a CalledProcessError with exit status 1.
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    output_lines = []

    if process.stdout is not None:
        for line in process.stdout:
            message = line.rstrip("\r\n")
            output_lines.append(message)
            logger.info("[SNAP] %s", message)

        process.stdout.close()

    return_code = process.wait()

    if return_code != 0:
        snap_output = "\n".join(output_lines[-100:])

        raise RuntimeError(
            "SNAP Goldstein phase filtering failed "
            f"(exit code {return_code}).\n"
            f"Graph: {graph_path}\n"
            f"SNAP output:\n{snap_output}"
        )

    validate_dim(output_dim)

    report = validate_phase_filtering(
        input_dim,
        output_dim,
        report_path=(output_dir / "phase_filtering_validation.json"),
    )

    summary = {
        "input": str(input_dim),
        "output": str(output_dim),
        "graph": str(graph_path),
        "alpha": alpha,
        "fft_size": fft_size,
        "window_size": window_size,
        "use_coherence_mask": use_coherence_mask,
        "validation_status": report.status,
    }

    summary_path = output_dir / "processing_summary.json"

    summary_path.write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "Phase filtering completed | output=%s | validation=%s",
        output_dim,
        report.status,
    )

    return output_dim
