from datetime import datetime
from logging import getLogger
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from utils.calculate_orbit import CalculateOrbit
from utils.coordinate_transform import geodetic_to_ecef
from utils.date_time_format import to_iso_utc

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANNOTATION_DIR = PROJECT_ROOT / "data" / "annotation"

SPEED_OF_LIGHT = 299_792_458.0

ANNOTATION_PATHS = [
    ANNOTATION_DIR
    / "s1a-iw3-slc-vv-20250104t181041-20250104t181107-057298-070cb8-006.xml",
    ANNOTATION_DIR
    / "s1a-iw3-slc-vv-20250116t181040-20250116t181106-057473-0713a0-006.xml",
]


@pytest.fixture
def calculator():
    return CalculateOrbit(logger=getLogger("test.zero_doppler_validation"))


@pytest.fixture
def reference_ground_position(calculator):
    """
    Select the reference target from the January 4 annotation.
    Reuse exactly the same ECEF target for both acquisitions.
    """
    reference_annotation = ANNOTATION_PATHS[0]

    assert reference_annotation.is_file()

    points = calculator.extract_geolocation_grid(reference_annotation)

    assert points, "Reference geolocation grid is empty"

    point = select_central_geolocation_point(points)

    return geodetic_to_ecef(
        latitude=point["latitude"],
        longitude=point["longitude"],
        height=point["height"],
    )


# @pytest.mark.parametrize(
#     "annotation_path",
#     ANNOTATION_PATHS,
#     ids=lambda path: path.stem,
# )
# def test_annotation_timestamp_diagnostics(annotation_path):
#     assert annotation_path.is_file()

#     root = ET.parse(annotation_path).getroot()

#     raw_orbit_times = [
#         element.text
#         for element in root.findall(".//orbitList/orbit/time")
#     ]

#     raw_geolocation_times = [
#         element.text
#         for element in root.findall(
#             ".//geolocationGridPointList/geolocationGridPoint/azimuthTime"
#         )
#     ]

#     assert raw_orbit_times
#     assert raw_geolocation_times

#     print(f"\nAnnotation: {annotation_path.name}")
#     print(f"First orbit time: {raw_orbit_times[0]!r}")
#     print(f"Last orbit time: {raw_orbit_times[-1]!r}")
#     print(f"First geolocation time: {raw_geolocation_times[0]!r}")
#     print(f"Last geolocation time: {raw_geolocation_times[-1]!r}")


# Validate timing
@pytest.mark.parametrize(
    "annotation_path",
    ANNOTATION_PATHS,
    ids=lambda path: path.stem,
)
def test_zero_doppler_time_within_annotation_coverage(
    calculator,
    reference_ground_position,
    annotation_path,
):
    assert annotation_path.is_file(), (
        f"Required annotation file not found: {annotation_path}"
    )

    orbit_vectors = calculator.extract_orbit_xml_file(annotation_path)

    geolocation_points = calculator.extract_geolocation_grid(annotation_path)

    assert geolocation_points, "Geolocation grid is empty"

    brackets = calculator.find_zero_doppler_brackets(
        orbit_vectors,
        reference_ground_position,
    )

    assert len(brackets) == 1, (
        f"Expected one zero-Doppler bracket, found {len(brackets)}"
    )

    first, second = brackets[0]

    zero_doppler_time = calculator.solve_zero_doppler_time(
        first=first,
        second=second,
        ground_position=reference_ground_position,
    )

    azimuth_times = [
        datetime.fromisoformat(to_iso_utc(point["azimuth_time"]))
        for point in geolocation_points
    ]

    start_time = min(azimuth_times)
    end_time = max(azimuth_times)

    print(
        f"\nAnnotation: {annotation_path.name}"
        f"\nStart azimuth time: {start_time}"
        f"\nEnd azimuth time: {end_time}"
        f"\nZero-Doppler time: {zero_doppler_time}"
    )

    assert start_time <= zero_doppler_time <= end_time, (
        f"Zero-Doppler time {zero_doppler_time} is outside "
        f"annotation azimuth coverage [{start_time}, {end_time}]"
    )


# Validate target coverage
def test_reference_point_slant_range_consistency(
    calculator,
    reference_ground_position,
):
    annotation_path = ANNOTATION_PATHS[0]

    assert annotation_path.is_file(), (
        f"Required annotation file not found: {annotation_path}"
    )

    # 1. Recover the same reference point used to define the
    # shared ground target.
    geolocation_points = calculator.extract_geolocation_grid(annotation_path)

    assert geolocation_points, "Geolocation grid is empty"

    reference_point = select_central_geolocation_point(geolocation_points)

    annotated_slant_range_time = reference_point["slant_range_time"]

    # 2. Find the target-specific zero-Doppler time.
    orbit_vectors = calculator.extract_orbit_xml_file(annotation_path)

    brackets = calculator.find_zero_doppler_brackets(
        orbit_vectors,
        reference_ground_position,
    )

    assert len(brackets) == 1, (
        f"Expected one zero-Doppler bracket, found {len(brackets)}"
    )

    first, second = brackets[0]

    zero_doppler_time = calculator.solve_zero_doppler_time(
        first=first,
        second=second,
        ground_position=reference_ground_position,
    )

    # 3. Interpolate the satellite position at zero Doppler.
    satellite_position, _ = calculator.interpolate_state(
        first,
        second,
        zero_doppler_time,
    )

    # 4. Convert geometric slant range to two-way travel time.
    geometric_slant_range = np.linalg.norm(
        reference_ground_position - satellite_position
    )

    calculated_slant_range_time = 2.0 * geometric_slant_range / SPEED_OF_LIGHT

    difference_seconds = calculated_slant_range_time - annotated_slant_range_time

    difference_microseconds = difference_seconds * 1e6

    equivalent_range_difference_m = difference_seconds * SPEED_OF_LIGHT / 2.0

    print(f"\nAnnotation: {annotation_path.name}")
    print(f"Reference pixel: {reference_point['pixel']}")
    print(f"Reference line: {reference_point['line']}")
    print(f"Reference azimuth time: {reference_point['azimuth_time']}")
    print(f"Zero-Doppler time: {zero_doppler_time}")
    print(f"Geometric slant range: {geometric_slant_range:.6f} m")
    print(f"Annotated slant-range time: {annotated_slant_range_time:.12f} s")
    print(f"Calculated slant-range time: {calculated_slant_range_time:.12f} s")
    print(f"Difference: {difference_microseconds:.6f} microseconds")
    print(f"Equivalent range difference: {equivalent_range_difference_m:.6f} m")

    # Basic physical sanity checks.
    assert geometric_slant_range > 0
    assert annotated_slant_range_time > 0
    assert np.isfinite(calculated_slant_range_time)
    assert np.isfinite(difference_seconds)

    # Provisional tolerance for this reference dataset.
    # This is an internal-consistency threshold, not a
    # Sentinel-1 absolute geolocation accuracy specification.
    MAX_RANGE_DIFFERENCE_M = 1.0

    assert abs(equivalent_range_difference_m) < MAX_RANGE_DIFFERENCE_M, (
        f"Slant-range mismatch: {equivalent_range_difference_m:.6f} m"
    )


# Select an interior geolocation point from the grid
def test_reference_point_is_interior(calculator):
    points = calculator.extract_geolocation_grid(ANNOTATION_PATHS[0])

    point = select_central_geolocation_point(points)

    lines = [p["line"] for p in points]
    pixels = [p["pixel"] for p in points]

    print(f"\nSelected line: {point['line']}")
    print(f"Selected pixel: {point['pixel']}")
    print(f"Latitude: {point['latitude']}")
    print(f"Longitude: {point['longitude']}")

    assert min(lines) < point["line"] < max(lines)
    assert min(pixels) < point["pixel"] < max(pixels)


def select_central_geolocation_point(
    points: list[dict],
) -> dict:
    if not points:
        raise ValueError("Geolocation grid is empty")

    lines = [point["line"] for point in points]
    pixels = [point["pixel"] for point in points]

    min_line, max_line = min(lines), max(lines)
    min_pixel, max_pixel = min(pixels), max(pixels)

    line_span = max_line - min_line
    pixel_span = max_pixel - min_pixel

    if line_span == 0 or pixel_span == 0:
        raise ValueError("Geolocation grid has no spatial extent")

    center_line = (min_line + max_line) / 2
    center_pixel = (min_pixel + max_pixel) / 2

    return min(
        points,
        key=lambda point: (
            ((point["line"] - center_line) / line_span) ** 2
            + ((point["pixel"] - center_pixel) / pixel_span) ** 2
        ),
    )


# Validate the target's range coverage
@pytest.mark.parametrize(
    "annotation_path",
    ANNOTATION_PATHS,
    ids=lambda path: path.stem,
)
def test_range_metadata_diagnostics(annotation_path):
    root = ET.parse(annotation_path).getroot()

    image_info = root.find(".//imageAnnotation/imageInformation")

    assert image_info is not None

    for field in (
        "slantRangeTime",
        "rangePixelSpacing",
        "numberOfSamples",
    ):
        value = image_info.findtext(field)
        assert value is not None, f"Missing {field}"

        print(f"{field}: {value}")


@pytest.mark.parametrize(
    "annotation_path",
    ANNOTATION_PATHS,
    ids=lambda path: path.stem,
)
def test_reference_target_within_range_samples(
    calculator,
    reference_ground_position,
    annotation_path,
):
    root = ET.parse(annotation_path).getroot()

    image_info = root.find(".//imageAnnotation/imageInformation")
    assert image_info is not None

    first_time = float(image_info.findtext("slantRangeTime"))
    pixel_spacing = float(image_info.findtext("rangePixelSpacing"))
    number_of_samples = int(image_info.findtext("numberOfSamples"))

    assert first_time > 0
    assert pixel_spacing > 0
    assert number_of_samples > 1

    last_time = (
        first_time + 2 * (number_of_samples - 1) * pixel_spacing / SPEED_OF_LIGHT
    )

    orbit_vectors = calculator.extract_orbit_xml_file(annotation_path)

    brackets = calculator.find_zero_doppler_brackets(
        orbit_vectors,
        reference_ground_position,
    )

    assert len(brackets) == 1
    first, second = brackets[0]

    zero_doppler_time = calculator.solve_zero_doppler_time(
        first,
        second,
        reference_ground_position,
    )

    satellite_position, _ = calculator.interpolate_state(
        first,
        second,
        zero_doppler_time,
    )

    slant_range = np.linalg.norm(reference_ground_position - satellite_position)

    target_time = 2 * slant_range / SPEED_OF_LIGHT

    # Continuous range-sample coordinate.
    target_pixel = (target_time - first_time) * SPEED_OF_LIGHT / (2 * pixel_spacing)

    print(f"\nAnnotation: {annotation_path.name}")
    print(f"First sample time: {first_time:.12f} s")
    print(f"Last sample time: {last_time:.12f} s")
    print(f"Target time: {target_time:.12f} s")
    print(f"Estimated range pixel: {target_pixel:.3f}")

    # Allow a provisional 1 m geometric consistency tolerance
    # at the sample boundaries.
    tolerance_pixels = 1.0 / pixel_spacing

    assert (
        -tolerance_pixels <= target_pixel <= (number_of_samples - 1 + tolerance_pixels)
    ), "Target lies outside range-sample coverage"


# Test burst metadata diagnostics
@pytest.mark.parametrize(
    "annotation_path",
    ANNOTATION_PATHS,
    ids=lambda path: path.stem,
)
def test_burst_metadata_diagnostics(annotation_path):
    root = ET.parse(annotation_path).getroot()

    swath_timing = root.find(".//swathTiming")
    assert swath_timing is not None

    for field in ("linesPerBurst", "burstList"):
        element = swath_timing.find(field)
        assert element is not None, f"Missing {field}"

    print(f"\nAnnotation: {annotation_path.name}")
    print("Lines per burst:", swath_timing.findtext("linesPerBurst"))
    print(
        "Azimuth time interval:",
        root.findtext(".//imageAnnotation/imageInformation/azimuthTimeInterval"),
    )

    bursts = swath_timing.findall("burstList/burst")
    print("Number of annotation bursts:", len(bursts))

    for index, burst in enumerate(bursts):
        print(
            f"Burst index {index}: "
            f"azimuthTime={burst.findtext('azimuthTime')}, "
            f"azimuthAnxTime={burst.findtext('azimuthAnxTime')}"
        )


@pytest.mark.parametrize(
    "annotation_path",
    ANNOTATION_PATHS,
    ids=lambda path: path.stem,
)
def test_burst_valid_sample_diagnostics(annotation_path):
    root = ET.parse(annotation_path).getroot()

    swath_timing = root.find(".//swathTiming")
    assert swath_timing is not None

    lines_per_burst = int(swath_timing.findtext("linesPerBurst"))

    bursts = swath_timing.findall("burstList/burst")

    print(f"\nAnnotation: {annotation_path.name}")
    print(f"Lines per burst: {lines_per_burst}")

    for burst_index in (3, 4):
        burst = bursts[burst_index]

        first_valid = [
            int(value) for value in burst.findtext("firstValidSample").split()
        ]

        last_valid = [int(value) for value in burst.findtext("lastValidSample").split()]

        assert len(first_valid) == lines_per_burst
        assert len(last_valid) == lines_per_burst

        valid_lines = [
            index
            for index, (first, last) in enumerate(zip(first_valid, last_valid))
            if first >= 0 and last >= first
        ]

        print(f"\nBurst index: {burst_index}")
        print(f"Valid azimuth lines: {len(valid_lines)}")

        if valid_lines:
            print(f"First valid line: {valid_lines[0]}")
            print(f"Last valid line: {valid_lines[-1]}")
            print(
                "Range bounds at first valid line:",
                first_valid[valid_lines[0]],
                last_valid[valid_lines[0]],
            )
            print(
                "Range bounds at last valid line:",
                first_valid[valid_lines[-1]],
                last_valid[valid_lines[-1]],
            )


@pytest.mark.parametrize(
    "annotation_path",
    ANNOTATION_PATHS,
    ids=lambda path: path.stem,
)
def test_reference_target_has_valid_burst_sample(
    calculator,
    reference_ground_position,
    annotation_path,
):
    root = ET.parse(annotation_path).getroot()

    swath_timing = root.find(".//swathTiming")
    image_info = root.find(".//imageAnnotation/imageInformation")

    assert swath_timing is not None
    assert image_info is not None

    lines_per_burst = int(swath_timing.findtext("linesPerBurst"))
    azimuth_interval = float(image_info.findtext("azimuthTimeInterval"))
    first_range_time = float(image_info.findtext("slantRangeTime"))
    range_spacing = float(image_info.findtext("rangePixelSpacing"))

    orbit_vectors = calculator.extract_orbit_xml_file(annotation_path)
    brackets = calculator.find_zero_doppler_brackets(
        orbit_vectors, reference_ground_position
    )
    assert len(brackets) == 1

    first, second = brackets[0]

    zero_doppler_time = calculator.solve_zero_doppler_time(
        first, second, reference_ground_position
    )
    satellite_position, _ = calculator.interpolate_state(
        first, second, zero_doppler_time
    )

    target_range = np.linalg.norm(reference_ground_position - satellite_position)
    target_pixel = (
        target_range - first_range_time * SPEED_OF_LIGHT / 2
    ) / range_spacing

    valid_burst_indices = []

    for burst_index, burst in enumerate(swath_timing.findall("burstList/burst")):
        burst_start = datetime.fromisoformat(to_iso_utc(burst.findtext("azimuthTime")))

        local_line = (
            zero_doppler_time - burst_start
        ).total_seconds() / azimuth_interval

        if not 0 <= local_line < lines_per_burst:
            continue

        first_valid = [
            int(value) for value in burst.findtext("firstValidSample").split()
        ]
        last_valid = [int(value) for value in burst.findtext("lastValidSample").split()]

        assert len(first_valid) == lines_per_burst
        assert len(last_valid) == lines_per_burst

        # Inspect the nearest recorded azimuth line.
        line_index = round(local_line)

        if not 0 <= line_index < lines_per_burst:
            continue

        lower_pixel = first_valid[line_index]
        upper_pixel = last_valid[line_index]

        is_valid = (
            lower_pixel >= 0
            and upper_pixel >= lower_pixel
            and lower_pixel <= target_pixel <= upper_pixel
        )

        print(
            f"\nBurst index: {burst_index}"
            f"\nLocal line: {local_line:.3f}"
            f"\nNearest line index: {line_index}"
            f"\nTarget range pixel: {target_pixel:.3f}"
            f"\nValid range bounds: [{lower_pixel}, {upper_pixel}]"
            f"\nTarget valid: {is_valid}"
        )

        if is_valid:
            valid_burst_indices.append(burst_index)

    assert valid_burst_indices, (
        "Shared reference target has no valid sample in any annotation burst"
    )

    print(f"\nValid annotation burst indices: {valid_burst_indices}")
