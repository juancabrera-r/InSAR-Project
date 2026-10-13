from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class BaselineResult:
    total_m: float
    parallel_m: float
    perpendicular_m: float


def calculate_baseline_geometry(
    reference_position: NDArray[np.float64],
    secondary_position: NDArray[np.float64],
    ground_position: NDArray[np.float64],
) -> BaselineResult:
    """
    Calculate the unsigned perpendicular baseline between two SAR acquisitions.

    The baseline is projected onto the plane perpendicular to the reference
    satellite's line of sight (LOS) toward a common ground target.

    Parameters
    ----------
    reference_position : NDArray[np.float64]
        Reference satellite ECEF position, in metres.
    secondary_position : NDArray[np.float64]
        Secondary satellite ECEF position, in metres.
    ground_position : NDArray[np.float64]
        Common ground target ECEF position, in metres.

    Returns
    -------
    BaselineResult
        Baseline result containing total, parallel, and perpendicular baseline magnitudes, in metres (nonnegative).

    Raises
    ------
    ValueError
        If an input is not a finite 3D vector or the reference
        satellite and ground target occupy the same position.
    """

    def validate_vector(
        vector: NDArray[np.float64],
        name: str,
    ) -> NDArray[np.float64]:
        array = np.asarray(vector, dtype=np.float64)

        if array.shape != (3,):
            raise ValueError(f"{name} must have shape (3,), got {array.shape}")

        if not np.all(np.isfinite(array)):
            raise ValueError(f"{name} must contain only finite values")

        return array

    reference = validate_vector(reference_position, "reference_position")
    secondary = validate_vector(secondary_position, "secondary_position")
    ground = validate_vector(ground_position, "ground_position")

    # Satellite-to-satellite baseline vector (ECEF, metres).
    baseline_vector = secondary - reference

    # Reference satellite-to-ground line-of-sight vector.
    los_vector = ground - reference
    los_distance = np.linalg.norm(los_vector)

    if los_distance == 0:
        raise ValueError(
            "Reference satellite and ground target cannot have identical positions"
        )

    # Unit vector along the reference LOS.
    los_unit = los_vector / los_distance

    # Signed scalar projection onto the reference LOS.
    parallel_baseline = float(np.dot(baseline_vector, los_unit))

    # Vector projection onto the reference LOS.
    parallel_component = parallel_baseline * los_unit

    # Baseline component perpendicular to the LOS.
    perpendicular_component = baseline_vector - parallel_component

    return BaselineResult(
        total_m=float(np.linalg.norm(baseline_vector)),
        parallel_m=parallel_baseline,
        perpendicular_m=float(np.linalg.norm(perpendicular_component)),
    )
