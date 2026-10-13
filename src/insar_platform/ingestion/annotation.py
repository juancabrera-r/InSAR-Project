from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from xml.etree import ElementTree as ET


class AnnotationBurst(TypedDict):
    azimuth_time: str
    first_valid_samples: list[int]
    last_valid_samples: list[int]


def extract_annotation_bursts(
    xml_file_path: Path,
) -> list[AnnotationBurst]:
    """Extract and validate Sentinel-1 burst timing and sample boundaries."""

    root = ET.parse(xml_file_path).getroot()

    lines_per_burst_text = root.findtext(".//swathTiming/linesPerBurst")
    samples_per_burst_text = root.findtext(".//swathTiming/samplesPerBurst")

    if lines_per_burst_text is None:
        raise ValueError("Missing linesPerBurst")

    if samples_per_burst_text is None:
        raise ValueError("Missing samplesPerBurst")

    lines_per_burst = int(lines_per_burst_text)
    samples_per_burst = int(samples_per_burst_text)

    if lines_per_burst <= 0 or samples_per_burst <= 0:
        raise ValueError("Burst dimensions must be positive")

    burst_elements = root.findall(".//swathTiming/burstList/burst")

    if not burst_elements:
        raise ValueError(f"No bursts found in annotation: {xml_file_path}")

    bursts: list[AnnotationBurst] = []

    for index, element in enumerate(burst_elements):
        azimuth_time = element.findtext("azimuthTime")
        first_valid_text = element.findtext("firstValidSample")
        last_valid_text = element.findtext("lastValidSample")

        if not azimuth_time:
            raise ValueError(f"Missing azimuthTime in burst {index}")

        if not first_valid_text or not last_valid_text:
            raise ValueError(f"Missing valid-sample boundaries in burst {index}")

        try:
            first_valid_samples = [int(value) for value in first_valid_text.split()]
            last_valid_samples = [int(value) for value in last_valid_text.split()]
        except ValueError as exc:
            raise ValueError(
                f"Non-integer valid-sample boundary in burst {index}"
            ) from exc

        if (
            len(first_valid_samples) != lines_per_burst
            or len(last_valid_samples) != lines_per_burst
        ):
            raise ValueError(
                f"Burst {index}: expected {lines_per_burst} validity entries, "
                f"got {len(first_valid_samples)} first and "
                f"{len(last_valid_samples)} last entries"
            )

        for line_index, (first, last) in enumerate(
            zip(first_valid_samples, last_valid_samples)
        ):
            # -1 in both arrays indicates a line without valid samples.
            if first == -1 and last == -1:
                continue

            if not (0 <= first <= last < samples_per_burst):
                raise ValueError(
                    f"Burst {index}, line {line_index}: "
                    f"invalid sample boundaries ({first}, {last}) "
                    f"for {samples_per_burst} samples per burst"
                )

        bursts.append(
            {
                "azimuth_time": azimuth_time,
                "first_valid_samples": first_valid_samples,
                "last_valid_samples": last_valid_samples,
            }
        )

    return bursts


@dataclass(frozen=True)
class BaselineResult:
    azimuth_time_interval: float
    slant_range_time: float
    range_sampling_rate: float


def extract_image_sampling_metadata(xml_file_path) -> BaselineResult:

    root = ET.parse(xml_file_path).getroot()

    azimuth_time_interval = root.findtext(".//imageInformation/azimuthTimeInterval")
    slant_range_time = root.findtext(".//imageInformation/slantRangeTime")
    range_sampling_rate = root.findtext(".//productInformation/rangeSamplingRate")

    if any(
        value is None
        for value in (
            azimuth_time_interval,
            slant_range_time,
            range_sampling_rate,
        )
    ):
        raise ValueError("Missing required Sentinel-1 sampling metadata")

    return BaselineResult(
        azimuth_time_interval=float(azimuth_time_interval),
        slant_range_time=float(slant_range_time),
        range_sampling_rate=float(range_sampling_rate),
    )
