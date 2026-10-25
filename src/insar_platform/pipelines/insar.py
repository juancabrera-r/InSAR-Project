import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from pyproj import Transformer

from insar_platform.database.services.insar_baseline import (
    InSARBaselineRecord,
    register_insar_baseline,
)
from insar_platform.geometry.baseline_geometry import (
    calculate_baseline_geometry,
)
from insar_platform.geometry.calculate_orbit import CalculateOrbit
from insar_platform.geometry.orbit_models import (
    OrbitProvenance,
    OrbitSource,
    validate_orbit_coverage,
)
from insar_platform.geometry.orbit_selection import select_external_orbit
from insar_platform.pipelines.acquisition import AcquisitionResult
from insar_platform.pipelines.acquisition_geometry import (
    calculate_range_pixel,
    validate_range_pixel,
    zero_doppler,
)


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
    max_perpendicular_baseline_m: float | None = None,
    max_normalized_residual: float | None = None,
    satellite_names: set[str] | None = None,
    orbit_directory: Path | None = None,
) -> BaselineGeometryResult:

    # 1. Validate acquisitions
    if len(acquisitions) < 2:
        raise ValueError(
            f"At least 2 acquisitions are required for InSAR processing, "
            f"but found {len(acquisitions)}"
        )

    logger.info(
        "Processing %d acquisitions",
        len(acquisitions),
    )

    if acquisitions[0].product_id == acquisitions[1].product_id:
        raise ValueError("Reference and secondary acquisitions must be different")

    if orbit_directory is None or not orbit_directory.is_dir():
        raise FileNotFoundError(f"Orbit directory not found: {orbit_directory}")

    calculate_orbit = CalculateOrbit(logger)

    satellite_positions = {}
    reference_ground_position = None

    for acquisition in acquisitions:
        product_id = acquisition.product_id
        xml_file_path = acquisition.annotation_path
        sampling_metadata = acquisition.sampling_metadata

        measurement_width = calculate_orbit.extract_measurement_width(
            xml_file_path=xml_file_path
        )

        # 2. Extract orbit vectors
        annotation_vectors = calculate_orbit.extract_orbit_xml_file(
            xml_file_path=xml_file_path
        )

        orbit_vectors = annotation_vectors

        if len(orbit_vectors) < 2:
            raise ValueError(f"Product {product_id}: insufficient orbit vectors")

        orbit_start = orbit_vectors[0]["timestamp"]
        orbit_end = orbit_vectors[-1]["timestamp"]

        logger.info(
            "Product %s | Extracted %d orbit vectors | Orbit coverage: %s to %s",
            product_id,
            len(orbit_vectors),
            orbit_start,
            orbit_end,
        )

        # 3. Normalize acquisition timestamp
        raw_time = acquisition.acquisition_time

        acquisition_time = (
            raw_time
            if isinstance(raw_time, datetime)
            else datetime.fromisoformat(raw_time)
        )

        if acquisition_time.tzinfo is None:
            raise ValueError(f"Acquisition timestamp lacks timezone: {raw_time}")

        acquisition_time = acquisition_time.astimezone(UTC)

        # 4. Retrieve selected burst
        if catalogue_burst_id not in acquisition.burst_mapping:
            raise ValueError(
                f"Burst {catalogue_burst_id} not found for product {product_id}"
            )

        annotation_index = acquisition.burst_mapping[catalogue_burst_id]

        if not 0 <= annotation_index < len(acquisition.annotation_bursts):
            raise ValueError(
                f"Annotation index {annotation_index} "
                f"is out of range for product {product_id}"
            )

        selected_burst = acquisition.annotation_bursts[annotation_index]

        first_valid_samples = selected_burst.first_valid_samples
        last_valid_samples = selected_burst.last_valid_samples

        # 5. Validate sampling metadata
        azimuth_interval = sampling_metadata.azimuth_time_interval

        if not np.isfinite(azimuth_interval) or azimuth_interval <= 0:
            raise ValueError(
                f"Product {product_id}: invalid azimuth interval {azimuth_interval}"
            )

        number_of_lines = len(first_valid_samples)

        if number_of_lines == 0:
            raise ValueError(
                f"Product {product_id}: selected burst has no azimuth lines"
            )

        if len(last_valid_samples) != number_of_lines:
            raise ValueError(
                f"Product {product_id}: inconsistent burst validity arrays"
            )

        # 6. Calculate selected burst time interval
        burst_start = datetime.fromisoformat(selected_burst.azimuth_time)

        if burst_start.tzinfo is None:
            raise ValueError(
                f"Burst timestamp lacks timezone: {selected_burst.azimuth_time}"
            )

        burst_start = burst_start.astimezone(UTC)

        burst_end = burst_start + timedelta(
            seconds=(number_of_lines - 1) * azimuth_interval
        )

        logger.info(
            "Product %s | Burst %s | Lines: %d | Start: %s | End: %s",
            product_id,
            catalogue_burst_id,
            number_of_lines,
            burst_start,
            burst_end,
        )

        # 7. Validate orbit coverage
        validate_orbit_coverage(
            coverage_start=orbit_start,
            coverage_end=orbit_end,
            burst_start=burst_start,
            burst_end=burst_end,
        )

        if not orbit_start <= acquisition_time <= orbit_end:
            raise ValueError(
                f"Product {product_id}: acquisition time "
                f"{acquisition_time} is outside orbit coverage "
                f"[{orbit_start}, {orbit_end}]"
            )

        satellite = calculate_orbit.extract_satellite(xml_file_path=xml_file_path)

        if satellite_names is None:
            raise ValueError("Satellite names set is not provided")

        if satellite not in satellite_names:
            raise ValueError(f"Invalid Sentinel-1 satellite identifier: {satellite}")

        orbit_provenance = OrbitProvenance(
            source=OrbitSource.ANNOTATION,
            file_path=xml_file_path,
            coverage_start=orbit_start,
            coverage_end=orbit_end,
            satellite=satellite,
            checksum_sha256=calculate_file_sha256(xml_file_path),
        )

        logger.info(
            "Product %s | Orbit source: %s | File: %s | "
            "Coverage: %s to %s | SHA256: %s",
            product_id,
            orbit_provenance.source.value,
            orbit_provenance.file_path,
            orbit_provenance.coverage_start.isoformat(),
            orbit_provenance.coverage_end.isoformat(),
            orbit_provenance.checksum_sha256,
        )

        # 8. Validate and select external orbit
        external_orbit = (
            select_external_orbit(
                orbit_directory=orbit_directory,
                satellite=satellite,
                burst_start=burst_start,
                burst_end=burst_end,
            )
            if orbit_directory is not None
            else None
        )

        logger.info("Orbit directory: %s", orbit_directory)

        if orbit_directory is not None:
            logger.info(
                "Available orbit files: %s",
                list(orbit_directory.rglob("*.EOF")),
            )

        if external_orbit is not None:
            orbit_vectors = external_orbit.vectors
            orbit_source = external_orbit.source
            orbit_file_path = external_orbit.file_path
            orbit_start = external_orbit.validity_start
            orbit_end = external_orbit.validity_end
        else:
            orbit_vectors = annotation_vectors
            orbit_source = OrbitSource.ANNOTATION
            orbit_file_path = xml_file_path
            orbit_start = annotation_vectors[0]["timestamp"]
            orbit_end = annotation_vectors[-1]["timestamp"]

            logger.warning(
                "Product %s | No compatible external orbit found. "
                "Using annotation orbit.",
                product_id,
            )

        validate_orbit_coverage(
            coverage_start=orbit_start,
            coverage_end=orbit_end,
            burst_start=burst_start,
            burst_end=burst_end,
        )

        # Validate actual state-vector coverage independently.
        validate_orbit_coverage(
            coverage_start=orbit_vectors[0]["timestamp"],
            coverage_end=orbit_vectors[-1]["timestamp"],
            burst_start=burst_start,
            burst_end=burst_end,
        )

        orbit_provenance = OrbitProvenance(
            source=orbit_source,
            file_path=orbit_file_path,
            coverage_start=orbit_start,
            coverage_end=orbit_end,
            satellite=satellite,
            checksum_sha256=calculate_file_sha256(orbit_file_path),
        )

        logger.info(
            "Product %s | Selected orbit: %s | File: %s | SHA256: %s",
            product_id,
            orbit_provenance.source.value,
            orbit_provenance.file_path,
            orbit_provenance.checksum_sha256,
        )

        # 9. Interpolate satellite state
        first, second = calculate_orbit.find_bracketing_vectors(
            orbit_vectors=orbit_vectors,
            acquisition_time=acquisition_time,
        )

        catalogue_position, catalogue_velocity = calculate_orbit.interpolate_state(
            first=first,
            second=second,
            acquisition_time=acquisition_time,
        )

        # 10. Extract geolocation grid
        geolocation_points = calculate_orbit.extract_geolocation_grid(xml_file_path)

        if not geolocation_points:
            raise ValueError(f"No geolocation points for product {product_id}")

        # 11. Define common reference ground position
        valid_lines = [
            i
            for i, (first, last) in enumerate(
                zip(first_valid_samples, last_valid_samples)
            )
            if 0 <= first <= last
        ]

        if not valid_lines:
            raise ValueError(f"No valid azimuth lines for burst {catalogue_burst_id}")

        local_line = valid_lines[len(valid_lines) // 2]

        target_pixel = (
            first_valid_samples[local_line] + last_valid_samples[local_line]
        ) / 2

        target_global_line = annotation_index * number_of_lines + local_line

        logger.info(
            "Product %s | Burst %s | Annotation index: %d | "
            "Global line: %d | Local line: %d | Pixel: %.2f | "
            "Valid lines: %d/%d | Grid points: %d",
            product_id,
            catalogue_burst_id,
            annotation_index,
            target_global_line,
            local_line,
            target_pixel,
            len(valid_lines),
            number_of_lines,
            len(geolocation_points),
        )

        if reference_ground_position is None:
            reference_ground_position = calculate_orbit.interpolate_geolocation_point(
                points=geolocation_points,
                target_line=target_global_line,
                target_pixel=target_pixel,
            )

        if not np.all(np.isfinite(reference_ground_position)):
            raise ValueError(
                f"Invalid interpolated reference ground position "
                f"for burst {catalogue_burst_id}"
            )

        logger.info(
            "Reference ground position selected at "
            "burst %s, local line %d, global line %d, pixel %.1f",
            catalogue_burst_id,
            local_line,
            target_global_line,
            target_pixel,
        )

        logger.info(
            "Reference ground position ECEF (m): %s",
            reference_ground_position,
        )

        transformer = Transformer.from_crs(
            "EPSG:4978",
            "EPSG:4979",
            always_xy=True,
        )

        longitude, latitude, height = transformer.transform(*reference_ground_position)

        logger.info(
            "Reference ground position: Lat=%.6f°, Lon=%.6f°, Height=%.2f m",
            latitude,
            longitude,
            height,
        )

        # 11. Calculate zero-Doppler geometry
        if max_normalized_residual is not None:
            max_normalized_residual = float(max_normalized_residual)

            if not np.isfinite(max_normalized_residual) or max_normalized_residual <= 0:
                raise ValueError("max_normalized_residual must be finite and positive")

        result = zero_doppler(
            logger=logger,
            product_id=product_id,
            calculate_orbit=calculate_orbit,
            catalogue_position=catalogue_position,
            catalogue_velocity=catalogue_velocity,
            reference_ground_position=reference_ground_position,
            orbit_vectors=orbit_vectors,
            satellite_positions=satellite_positions,
            max_normalized_residual=max_normalized_residual,
            acquisition_start=burst_start,
            acquisition_end=burst_end,
        )

        # 12. Validate zero-Doppler azimuth line
        if result.time.tzinfo is None:
            raise ValueError(f"Product {product_id}: zero-Doppler time lacks timezone")

        target_time = result.time.astimezone(UTC)

        local_line = (target_time - burst_start).total_seconds() / azimuth_interval

        if not np.isfinite(local_line):
            raise ValueError(f"Product {product_id}: invalid zero-Doppler line")

        line_index = round(local_line)

        if not 0 <= line_index < number_of_lines:
            raise ValueError(
                f"Product {product_id}: zero-Doppler line "
                f"{line_index} is outside burst {catalogue_burst_id}"
            )

        # 13. Validate reference ground point range coverage
        first_valid = first_valid_samples[line_index]
        last_valid = last_valid_samples[line_index]

        range_result = calculate_range_pixel(
            satellite_position=result.satellite_position,
            ground_position=reference_ground_position,
            slant_range_time_s=sampling_metadata.slant_range_time,
            range_sampling_rate_hz=sampling_metadata.range_sampling_rate,
        )

        validate_range_pixel(
            range_pixel=range_result.range_pixel,
            first_valid_sample=first_valid,
            last_valid_sample=last_valid,
            measurement_width=measurement_width,
        )

        logger.info(
            "Product %s | Burst %s | Azimuth line %d | "
            "Range pixel %.3f | Valid range [%d, %d] | "
            "Slant range %.3f m",
            product_id,
            catalogue_burst_id,
            line_index,
            range_result.range_pixel,
            first_valid,
            last_valid,
            range_result.slant_range_m,
        )

        satellite_positions[product_id] = result.satellite_position

    # 14. Calculate baseline geometry
    reference_id = acquisitions[0].product_id
    secondary_id = acquisitions[1].product_id

    if reference_ground_position is None:
        raise ValueError("Reference ground position is not defined")

    baseline = calculate_baseline_geometry(
        reference_position=satellite_positions[reference_id],
        secondary_position=satellite_positions[secondary_id],
        ground_position=reference_ground_position,
    )

    if max_perpendicular_baseline_m is not None:
        if (
            not np.isfinite(max_perpendicular_baseline_m)
            or max_perpendicular_baseline_m <= 0
        ):
            raise ValueError("max_perpendicular_baseline_m must be finite and positive")

        if abs(baseline.perpendicular_m) > max_perpendicular_baseline_m:
            raise ValueError(
                f"Perpendicular baseline "
                f"{baseline.perpendicular_m:.3f} m exceeds "
                f"maximum {max_perpendicular_baseline_m:.3f} m"
            )

    # 15. Validate baseline decomposition
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

    # 16. Register baseline in database
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


def calculate_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()
