import math
from dataclasses import asdict, dataclass
from logging import Logger
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from insar_platform.utils.date_time_format import to_iso_utc


@dataclass(frozen=True)
class AnnotationBurst:
    azimuth_time: str
    first_valid_samples: list[int]
    last_valid_samples: list[int]


@dataclass(frozen=True)
class BaselineResult:
    azimuth_time_interval: float
    slant_range_time: float
    range_sampling_rate: float


class AnnotationManager:
    def __init__(
        self,
        annotation_path: Path,
        logger: Logger,
    ):
        self.annotation_path = annotation_path
        self.logger = logger

    def extract_annotation_bursts(
        self,
        root: ET.Element,
    ) -> tuple[list[AnnotationBurst], int, int]:

        lines_text = root.findtext(".//swathTiming/linesPerBurst")
        samples_text = root.findtext(".//swathTiming/samplesPerBurst")

        if lines_text is None or samples_text is None:
            raise ValueError("Missing burst dimensions")

        lines_per_burst = int(lines_text)
        samples_per_burst = int(samples_text)

        if lines_per_burst <= 0 or samples_per_burst <= 0:
            raise ValueError("Burst dimensions must be positive")

        burst_elements = root.findall(".//swathTiming/burstList/burst")

        if not burst_elements:
            raise ValueError("No bursts found in annotation XML")

        bursts: list[AnnotationBurst] = []

        for index, element in enumerate(burst_elements):
            azimuth_time = element.findtext("azimuthTime")
            first_text = element.findtext("firstValidSample")
            last_text = element.findtext("lastValidSample")

            if not azimuth_time:
                raise ValueError(f"Missing azimuthTime in burst {index}")

            if not first_text or not last_text:
                raise ValueError(f"Missing sample boundaries in burst {index}")

            try:
                first = [int(v) for v in first_text.split()]
                last = [int(v) for v in last_text.split()]
            except ValueError as exc:
                raise ValueError(
                    f"Non-integer sample boundary in burst {index}"
                ) from exc

            if len(first) != lines_per_burst or len(last) != lines_per_burst:
                raise ValueError(
                    f"Burst {index}: expected {lines_per_burst} lines, "
                    f"got {len(first)} first and {len(last)} last"
                )

            for line_index, (f, l) in enumerate(zip(first, last)):
                if f == -1 and l == -1:
                    continue

                if not (0 <= f <= l < samples_per_burst):
                    raise ValueError(
                        f"Burst {index}, line {line_index}: "
                        f"invalid boundaries ({f}, {l})"
                    )

            bursts.append(
                AnnotationBurst(
                    azimuth_time=to_iso_utc(azimuth_time),
                    first_valid_samples=first,
                    last_valid_samples=last,
                )
            )

        return bursts, lines_per_burst, samples_per_burst

    def extract_image_sampling_metadata(
        self,
        root: ET.Element,
    ) -> BaselineResult:

        azimuth_text = root.findtext(".//imageInformation/azimuthTimeInterval")
        slant_text = root.findtext(".//imageInformation/slantRangeTime")
        range_text = root.findtext(".//productInformation/rangeSamplingRate")

        if azimuth_text is None or slant_text is None or range_text is None:
            raise ValueError("Missing required Sentinel-1 sampling metadata")

        result = BaselineResult(
            azimuth_time_interval=float(azimuth_text),
            slant_range_time=float(slant_text),
            range_sampling_rate=float(range_text),
        )

        if (
            not math.isfinite(result.azimuth_time_interval)
            or result.azimuth_time_interval <= 0
            or not math.isfinite(result.slant_range_time)
            or result.slant_range_time < 0
            or not math.isfinite(result.range_sampling_rate)
            or result.range_sampling_rate <= 0
        ):
            raise ValueError("Invalid Sentinel-1 sampling metadata")

        return result

    def inspect_annotation(self) -> dict[str, Any]:
        self.logger.info(
            "Inspecting annotation XML: %s",
            self.annotation_path,
        )

        root = ET.parse(self.annotation_path).getroot()

        bursts, lines_per_burst, samples_per_burst = self.extract_annotation_bursts(
            root
        )

        sampling = self.extract_image_sampling_metadata(root)

        total_pixels = lines_per_burst * samples_per_burst

        burst_statistics = []

        for index, burst in enumerate(bursts):
            first = burst.first_valid_samples
            last = burst.last_valid_samples

            valid_lines = sum(f >= 0 for f in first)

            valid_pixels = sum(l - f + 1 for f, l in zip(first, last) if f >= 0)

            statistics = {
                "burst_index": index,
                "azimuth_time": burst.azimuth_time,
                "valid_lines": valid_lines,
                "total_lines": lines_per_burst,
                "valid_pixels": valid_pixels,
                "total_pixels": total_pixels,
                "valid_fraction": valid_pixels / total_pixels,
            }

            burst_statistics.append(statistics)

            self.logger.info(
                "Burst %d | valid lines=%d/%d | valid pixels=%d/%d (%.2f%%)",
                index,
                valid_lines,
                lines_per_burst,
                valid_pixels,
                total_pixels,
                100 * statistics["valid_fraction"],
            )

        return {
            "lines_per_burst": lines_per_burst,
            "samples_per_burst": samples_per_burst,
            "burst_count": len(bursts),
            "sampling": asdict(sampling),
            "bursts": burst_statistics,
        }
