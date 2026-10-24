from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

SPEED_OF_LIGHT_M_S = 299_792_458.0


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
    max_normalized_residual,
    acquisition_start: datetime,
    acquisition_end: datetime,
) -> ZeroDopplerResult:

    if max_normalized_residual is not None:
        if isinstance(max_normalized_residual, bool):
            raise TypeError("max_normalized_residual must be a number")

        try:
            max_normalized_residual = float(max_normalized_residual)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "max_normalized_residual must be a valid number or None"
            ) from exc

        if not np.isfinite(max_normalized_residual) or max_normalized_residual <= 0:
            raise ValueError("max_normalized_residual must be finite and positive")

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

    # 2. Restrict orbit vectors to the acquisition interval,
    # retaining one vector on either side for interpolation.

    if acquisition_start >= acquisition_end:
        raise ValueError("Invalid acquisition time interval")

    orbit_vectors = sorted(
        orbit_vectors,
        key=lambda vector: vector["timestamp"],
    )

    timestamps = [vector["timestamp"] for vector in orbit_vectors]

    if acquisition_start < timestamps[0] or acquisition_end > timestamps[-1]:
        raise ValueError(
            f"Orbit coverage does not contain acquisition interval "
            f"{acquisition_start}–{acquisition_end}"
        )

    brackets = calculate_orbit.find_zero_doppler_brackets(
        orbit_vectors=orbit_vectors,
        ground_position=reference_ground_position,
    )

    valid_brackets = [
        (first, second)
        for first, second in brackets
        if first["timestamp"] <= acquisition_end
        and second["timestamp"] >= acquisition_start
    ]

    logger.info(
        "Product %s | Found %d brackets, %d within acquisition",
        product_id,
        len(brackets),
        len(valid_brackets),
    )

    if len(valid_brackets) != 1:
        raise ValueError(
            f"Expected exactly one zero-Doppler bracket within "
            f"acquisition interval for {product_id}, "
            f"found {len(valid_brackets)}"
        )

    first, second = valid_brackets[0]

    # 3. Solve for the zero-Doppler time within the identified bracket
    zero_doppler_time = calculate_orbit.solve_zero_doppler_time(
        first=first,
        second=second,
        ground_position=reference_ground_position,
    )

    if not acquisition_start <= zero_doppler_time <= acquisition_end:
        raise ValueError(
            f"Zero-Doppler time {zero_doppler_time} falls outside "
            f"acquisition interval "
            f"[{acquisition_start}, {acquisition_end}]"
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

    # 5. Compute the normalized zero-Doppler residual
    line_of_sight = reference_ground_position - zero_doppler_position

    los_norm = float(np.linalg.norm(line_of_sight))
    velocity_norm = float(np.linalg.norm(zero_doppler_velocity))

    if (
        not np.isfinite(los_norm)
        or not np.isfinite(velocity_norm)
        or los_norm <= 0.0
        or velocity_norm <= 0.0
    ):
        raise ValueError(
            f"Product {product_id} | Invalid geometry for normalized residual"
        )

    normalized_residual = float(abs(residual) / (los_norm * velocity_norm))

    if not np.isfinite(normalized_residual):
        raise ValueError("Normalized residual must be finite")

    logger.info(
        "Product %s | residual=%.12e | LOS norm=%.6f | "
        "velocity norm=%.6f | normalized=%.12e",
        product_id,
        residual,
        los_norm,
        velocity_norm,
        normalized_residual,
    )

    if (
        max_normalized_residual is not None
        and normalized_residual > max_normalized_residual
    ):
        raise ValueError(
            f"Normalized residual {normalized_residual:.6e} exceeds "
            f"maximum {max_normalized_residual:.6e}"
        )

    return ZeroDopplerResult(
        time=zero_doppler_time,
        satellite_position=zero_doppler_position,
        satellite_velocity=zero_doppler_velocity,
        residual=residual,
        normalized_residual=normalized_residual,
    )


@dataclass(frozen=True)
class RangePixelResult:
    slant_range_m: float
    slant_range_time_s: float
    range_pixel: float


def calculate_range_pixel(
    satellite_position: np.ndarray,
    ground_position: np.ndarray,
    slant_range_time_s: float,
    range_sampling_rate_hz: float,
) -> RangePixelResult:
    satellite = np.asarray(satellite_position, dtype=float)
    ground = np.asarray(ground_position, dtype=float)

    if satellite.shape != (3,) or ground.shape != (3,):
        raise ValueError("Expected 3D ECEF positions")

    if not np.all(np.isfinite(satellite)) or not np.all(np.isfinite(ground)):
        raise ValueError("Non-finite ECEF position")

    if not np.isfinite(slant_range_time_s) or slant_range_time_s < 0:
        raise ValueError("Invalid first-sample slant-range time")

    if not np.isfinite(range_sampling_rate_hz) or range_sampling_rate_hz <= 0:
        raise ValueError("Invalid range sampling rate")

    slant_range_m = float(np.linalg.norm(satellite - ground))
    target_time_s = 2.0 * slant_range_m / SPEED_OF_LIGHT_M_S

    range_pixel = (target_time_s - slant_range_time_s) * range_sampling_rate_hz

    if not np.isfinite(range_pixel):
        raise ValueError("Non-finite range pixel")

    return RangePixelResult(
        slant_range_m=slant_range_m,
        slant_range_time_s=target_time_s,
        range_pixel=float(range_pixel),
    )


def validate_range_pixel(
    range_pixel: float,
    first_valid_sample: int,
    last_valid_sample: int,
    measurement_width: int,
) -> None:
    if measurement_width <= 0:
        raise ValueError("Invalid measurement width")

    if not 0 <= first_valid_sample <= last_valid_sample < measurement_width:
        raise ValueError("Invalid range validity interval")

    if not np.isfinite(range_pixel):
        raise ValueError("Non-finite range pixel")

    if not first_valid_sample <= range_pixel <= last_valid_sample:
        raise ValueError(
            f"Reference ground point outside valid range: "
            f"pixel={range_pixel:.3f}, "
            f"valid=[{first_valid_sample}, {last_valid_sample}]"
        )
