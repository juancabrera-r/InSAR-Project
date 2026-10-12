from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class ZeroDopplerResult:
    time: datetime
    satellite_position: NDArray[np.float64]
    satellite_velocity: NDArray[np.float64]
    residual: float
    normalized_residual: float


def zero_doppler(
    logger,
    product_id,
    calculate_orbit,
    catalogue_position,
    catalogue_velocity,
    reference_ground_position,
    orbit_vectors,
    satellite_positions,
) -> ZeroDopplerResult:

    zero_doppler_residual = calculate_orbit.zero_doppler_residual(
        satellite_position=catalogue_position,
        satellite_velocity=catalogue_velocity,  # Using the interpolated satellite velocity
        ground_position=reference_ground_position,
    )

    logger.info(
        "Product %s | Zero Doppler residual (m²/s): %f",
        product_id,
        zero_doppler_residual,
    )

    # 1. Evaluate zero-Doppler residual at orbit coverage endpoints
    first_vector = orbit_vectors[0]
    last_vector = orbit_vectors[-1]

    start_position = np.array(
        [first_vector["position"][axis] for axis in ("x", "y", "z")],
        dtype=float,
    )
    start_velocity = np.array(
        [first_vector["velocity"][axis] for axis in ("x", "y", "z")],
        dtype=float,
    )

    end_position = np.array(
        [last_vector["position"][axis] for axis in ("x", "y", "z")],
        dtype=float,
    )
    end_velocity = np.array(
        [last_vector["velocity"][axis] for axis in ("x", "y", "z")],
        dtype=float,
    )

    residual_start = calculate_orbit.zero_doppler_residual(
        satellite_position=start_position,
        satellite_velocity=start_velocity,
        ground_position=reference_ground_position,
    )

    residual_end = calculate_orbit.zero_doppler_residual(
        satellite_position=end_position,
        satellite_velocity=end_velocity,
        ground_position=reference_ground_position,
    )

    logger.info(
        "Product %s | Start residual: %.3f m²/s | End residual: %.3f m²/s",
        product_id,
        residual_start,
        residual_end,
    )

    if residual_start * residual_end < 0:
        logger.info(
            "Product %s | Zero-Doppler root is bracketed",
            product_id,
        )
    elif residual_start == 0 or residual_end == 0:
        logger.info(
            "Product %s | Zero-Doppler condition satisfied at an endpoint",
            product_id,
        )
    else:
        logger.warning(
            "Product %s | No zero-Doppler sign change between coverage endpoints",
            product_id,
        )

    # 2. Find and log zero-Doppler brackets within the orbit coverage
    brackets = calculate_orbit.find_zero_doppler_brackets(
        orbit_vectors=orbit_vectors,
        ground_position=reference_ground_position,
    )

    for first, second in brackets:
        logger.info(
            "Product %s | Zero-Doppler bracket: %s to %s",
            product_id,
            first["timestamp"],
            second["timestamp"],
        )

    if len(brackets) != 1:
        raise ValueError(
            f"Expected exactly one zero-Doppler bracket for {product_id}, "
            f"found {len(brackets)}"
        )

    first, second = brackets[0]

    # 3. Solve for the zero-Doppler time within the identified bracket
    zero_doppler_time = calculate_orbit.solve_zero_doppler_time(
        first=first,
        second=second,
        ground_position=reference_ground_position,
    )

    logger.info(
        "Product %s | Zero-Doppler time: %s",
        product_id,
        zero_doppler_time,
    )

    # 4. Interpolate the satellite state at the zero-Doppler time and compute the residual
    zero_doppler_position, zero_doppler_velocity = calculate_orbit.interpolate_state(
        first,
        second,
        zero_doppler_time,
    )

    satellite_positions[product_id] = zero_doppler_position

    residual = calculate_orbit.zero_doppler_residual(
        satellite_position=zero_doppler_position,
        satellite_velocity=zero_doppler_velocity,
        ground_position=reference_ground_position,
    )

    logger.info(
        "Product %s | Solved zero-Doppler residual: %.6f m²/s",
        product_id,
        residual,
    )

    # 5. Compute the line-of-sight vector and the normalized zero-Doppler residual
    line_of_sight = reference_ground_position - zero_doppler_position

    normalized_residual = abs(residual) / (
        np.linalg.norm(line_of_sight) * np.linalg.norm(zero_doppler_velocity)
    )

    logger.info(
        "Product %s | residual=%.12e | LOS norm=%.6f | velocity norm=%.6f | normalized=%.12e",
        product_id,
        residual,
        np.linalg.norm(line_of_sight),
        np.linalg.norm(zero_doppler_velocity),
        normalized_residual,
    )

    return ZeroDopplerResult(
        time=zero_doppler_time,
        satellite_position=zero_doppler_position,
        satellite_velocity=zero_doppler_velocity,
        residual=residual,
        normalized_residual=normalized_residual,
    )
