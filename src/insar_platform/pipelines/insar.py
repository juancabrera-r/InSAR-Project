import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from insar_platform.database.services.insar_baseline import (
    InSARBaselineRecord,
    register_insar_baseline,
)
from insar_platform.geometry.baseline_geometry import (
    calculate_baseline_geometry,
)
from insar_platform.geometry.calculate_orbit import CalculateOrbit
from insar_platform.geometry.coordinate_transform import geodetic_to_ecef
from insar_platform.pipelines.acquisition import AcquisitionResult
from insar_platform.pipelines.acquisition_geometry import zero_doppler


@dataclass(frozen=True)
class BaselineGeometryResult:
    total_m: float
    perpendicular_m: float
    parallel_m: float


def insar_processing_pipeline(
    acquisitions: list[AcquisitionResult],
    logger: logging.Logger,
    catalogue_burst_id: int = 219,
    run_id: uuid.UUID | None = None,
) -> BaselineGeometryResult:

    if len(acquisitions) != 2:
        raise ValueError(f"Expected 2 acquisitions, found {len(acquisitions)}")

    calculate_orbit = CalculateOrbit(logger)

    satellite_positions = {}
    reference_ground_position = None

    for acquisition in acquisitions:
        product_id = acquisition.product_id
        xml_file_path = acquisition.annotation_path
        sampling_metadata = acquisition.sampling_metadata

        # 1. Extract orbit vectors
        orbit_vectors = calculate_orbit.extract_orbit_xml_file(
            xml_file_path=xml_file_path
        )

        logger.info(
            "Product %s | Extracted %d orbit vectors",
            product_id,
            len(orbit_vectors),
        )

        # 2. Normalize acquisition timestamp
        raw_time = acquisition.acquisition_time

        if isinstance(raw_time, datetime):
            acquisition_time = raw_time
        else:
            acquisition_time = datetime.fromisoformat(raw_time)

        if acquisition_time.tzinfo is None:
            raise ValueError(f"Acquisition timestamp lacks timezone: {raw_time}")

        acquisition_time = acquisition_time.astimezone(UTC)

        # 3. Validate orbit coverage
        orbit_start = orbit_vectors[0]["timestamp"]
        orbit_end = orbit_vectors[-1]["timestamp"]

        logger.info(
            "Product %s | Orbit coverage: %s to %s",
            product_id,
            orbit_start,
            orbit_end,
        )

        if not orbit_start <= acquisition_time <= orbit_end:
            raise ValueError(
                f"Acquisition {acquisition_time} is outside "
                f"orbit coverage [{orbit_start}, {orbit_end}]"
            )

        # 4. Interpolate satellite position
        first, second = calculate_orbit.find_bracketing_vectors(
            orbit_vectors=orbit_vectors,
            acquisition_time=acquisition_time,
        )

        catalogue_position, catalogue_velocity = calculate_orbit.interpolate_state(
            first=first,
            second=second,
            acquisition_time=acquisition_time,
        )

        # 5. Extract geolocation grid
        geolocation_points = calculate_orbit.extract_geolocation_grid(xml_file_path)

        if not geolocation_points:
            raise ValueError(f"No geolocation points for product {product_id}")

        # 6. Define reference ground position once
        if reference_ground_position is None:
            point = calculate_orbit.select_central_geolocation_point(geolocation_points)

            reference_ground_position = geodetic_to_ecef(
                latitude=point["latitude"],
                longitude=point["longitude"],
                height=point["height"],
            )

            logger.info(
                "Reference ground position ECEF (m): %s",
                reference_ground_position,
            )

        # 7. Zero-Doppler geometry
        result = zero_doppler(
            logger=logger,
            product_id=product_id,
            calculate_orbit=calculate_orbit,
            catalogue_position=catalogue_position,
            catalogue_velocity=catalogue_velocity,
            reference_ground_position=reference_ground_position,
            orbit_vectors=orbit_vectors,
            satellite_positions=satellite_positions,
        )

        # 8. Retrieve selected burst
        if catalogue_burst_id not in acquisition.burst_mapping:
            raise ValueError(
                f"Burst {catalogue_burst_id} not found for product {product_id}"
            )

        annotation_index = acquisition.burst_mapping[catalogue_burst_id]

        selected_burst = acquisition.annotation_bursts[annotation_index]

        first_valid_samples = selected_burst["first_valid_samples"]
        last_valid_samples = selected_burst["last_valid_samples"]

        # 9. Calculate local azimuth line
        burst_start = datetime.fromisoformat(selected_burst["azimuth_time"])

        if burst_start.tzinfo is None:
            raise ValueError(
                f"Burst timestamp lacks timezone: {selected_burst['azimuth_time']}"
            )

        burst_start = burst_start.astimezone(UTC)
        target_time = result.time.astimezone(UTC)

        local_line = (
            target_time - burst_start
        ).total_seconds() / sampling_metadata.azimuth_time_interval

        line_index = round(local_line)

        if not 0 <= line_index < len(first_valid_samples):
            raise ValueError(
                f"Target azimuth line {line_index} "
                f"is outside burst {catalogue_burst_id}"
            )

        first_valid = first_valid_samples[line_index]
        last_valid = last_valid_samples[line_index]

        logger.info(
            "Product %s | Burst %s | Line %d | Valid range samples: %d to %d",
            product_id,
            catalogue_burst_id,
            line_index,
            first_valid,
            last_valid,
        )

        satellite_positions[product_id] = result.satellite_position

    # 10. Calculate baseline geometry
    reference_id = acquisitions[0].product_id
    secondary_id = acquisitions[1].product_id

    if reference_ground_position is None:
        raise ValueError("Reference ground position is not defined.")

    baseline = calculate_baseline_geometry(
        reference_position=satellite_positions[reference_id],
        secondary_position=satellite_positions[secondary_id],
        ground_position=reference_ground_position,
    )

    if not np.isclose(
        baseline.total_m**2,
        baseline.parallel_m**2 + baseline.perpendicular_m**2,
        rtol=1e-10,
        atol=1e-6,
    ):
        raise ValueError("Baseline decomposition validation failed")

    logger.info(
        "Reference: %s | Secondary: %s | "
        "Total baseline: %.6f m | "
        "Parallel baseline: %.6f m | "
        "Perpendicular baseline: %.6f m",
        reference_id,
        secondary_id,
        baseline.total_m,
        baseline.parallel_m,
        baseline.perpendicular_m,
    )

    if run_id is None:
        raise ValueError(
            "InSAR processing run_id is required for database registration"
        )

    record = InSARBaselineRecord(
        reference_product_name=acquisitions[0].product_name,
        secondary_product_name=acquisitions[1].product_name,
        catalogue_burst_id=catalogue_burst_id,
        total_m=baseline.total_m,
        perpendicular_m=baseline.perpendicular_m,
        parallel_m=baseline.parallel_m,
    )

    baseline_id = register_insar_baseline(
        run_id=run_id,
        record=record,
    )

    logger.info(
        "Registered InSAR baseline: %s | Run ID: %s",
        baseline_id,
        run_id,
    )

    return BaselineGeometryResult(
        total_m=baseline.total_m,
        perpendicular_m=baseline.perpendicular_m,
        parallel_m=baseline.parallel_m,
    )
