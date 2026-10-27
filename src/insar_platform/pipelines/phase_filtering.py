"""Goldstein phase filtering for Sentinel-1 interferograms."""

import logging
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

LOG = logging.getLogger(__name__)


def validate_dim(path: Path) -> None:
    """Check that a BEAM-DIMAP product exists."""

    if not path.is_file():
        raise FileNotFoundError(f"SNAP product not found: {path}")

    data_dir = path.with_suffix(".data")

    if not data_dir.is_dir():
        raise FileNotFoundError(f"SNAP data directory not found: {data_dir}")

    if not any(data_dir.iterdir()):
        raise RuntimeError(f"SNAP data directory is empty: {data_dir}")


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
    """Create the SNAP GoldsteinPhaseFiltering graph."""

    graph = ET.Element("graph", id="goldstein-phase-filtering")
    ET.SubElement(graph, "version").text = "1.0"

    read = ET.SubElement(graph, "node", id="Read")
    ET.SubElement(read, "operator").text = "Read"
    read_parameters = ET.SubElement(read, "parameters")
    ET.SubElement(read_parameters, "file").text = str(input_dim)

    goldstein = ET.SubElement(graph, "node", id="Goldstein")
    ET.SubElement(goldstein, "operator").text = "GoldsteinPhaseFiltering"

    sources = ET.SubElement(goldstein, "sources")
    ET.SubElement(sources, "sourceProduct", refid="Read")

    parameters = ET.SubElement(goldstein, "parameters")
    ET.SubElement(parameters, "alpha").text = str(alpha)
    ET.SubElement(parameters, "FFTSizeString").text = str(fft_size)
    ET.SubElement(parameters, "windowSizeString").text = str(window_size)
    ET.SubElement(parameters, "useCoherenceMask").text = str(use_coherence_mask).lower()

    write = ET.SubElement(graph, "node", id="Write")
    ET.SubElement(write, "operator").text = "Write"

    write_sources = ET.SubElement(write, "sources")
    ET.SubElement(write_sources, "sourceProduct", refid="Goldstein")

    write_parameters = ET.SubElement(write, "parameters")
    ET.SubElement(write_parameters, "file").text = str(output_dim)
    ET.SubElement(write_parameters, "formatName").text = "BEAM-DIMAP"

    graph_path.parent.mkdir(parents=True, exist_ok=True)

    ET.ElementTree(graph).write(
        graph_path,
        encoding="utf-8",
        xml_declaration=True,
    )


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
    """Apply Goldstein phase filtering to a debursted interferogram."""

    input_dim = Path(interferogram_dim).resolve()
    output_dir = Path(output_dir).resolve()

    validate_dim(input_dim)

    if not 0.0 <= alpha <= 1.0:
        raise ValueError("Goldstein alpha must be between 0 and 1")

    if fft_size <= 0 or fft_size & (fft_size - 1):
        raise ValueError("FFT size must be a positive power of two")

    if window_size < 1:
        raise ValueError("Window size must be positive")

    if threads < 1:
        raise ValueError("SNAP threads must be >= 1")

    executable = shutil.which(gpt)

    if executable is None:
        raise FileNotFoundError(f"SNAP GPT executable not found: {gpt}")

    output_dir.mkdir(parents=True, exist_ok=True)

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

    logger.info(
        "Goldstein phase filtering | alpha=%.2f | FFT=%d | window=%d",
        alpha,
        fft_size,
        window_size,
    )

    subprocess.run(
        [
            executable,
            str(graph_path),
            "-c",
            cache,
            "-q",
            str(threads),
        ],
        check=True,
    )

    validate_dim(output_dim)

    logger.info("Phase filtering completed: %s", output_dim)

    return output_dim
