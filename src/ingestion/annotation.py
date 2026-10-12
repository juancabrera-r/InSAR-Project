from pathlib import Path
from xml.etree import ElementTree as ET


def extract_annotation_bursts(
    xml_file_path: Path,
) -> list[dict[str, str]]:
    """Extract azimuth timestamps from Sentinel-1 annotation bursts."""

    root = ET.parse(xml_file_path)

    burst_elements = root.findall(".//swathTiming/burstList/burst")

    if not burst_elements:
        raise ValueError(f"No bursts found in annotation: {xml_file_path}")

    bursts = []

    for index, element in enumerate(burst_elements):
        azimuth_time = element.findtext("azimuthTime")

        if not azimuth_time:
            raise ValueError(f"Missing azimuthTime in burst {index}")

        bursts.append({"azimuth_time": azimuth_time})

    return bursts
