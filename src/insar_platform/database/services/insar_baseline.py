import math
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from insar_platform.database.connection import SessionLocal
from insar_platform.database.models.insar_baseline import InSARBaseline
from insar_platform.database.models.sar_product import SARProduct


@dataclass(frozen=True)
class InSARBaselineRecord:
    reference_product_name: str
    secondary_product_name: str
    catalogue_burst_id: int
    total_m: float
    perpendicular_m: float
    parallel_m: float


def register_insar_baseline(
    run_id: uuid.UUID,
    record: InSARBaselineRecord,
) -> uuid.UUID:
    """
    Register an InSAR baseline calculation.

    Returns the UUID of the inserted or existing baseline.
    """
    if record.reference_product_name == record.secondary_product_name:
        raise ValueError("Reference and secondary products must be different.")

    values = (
        record.total_m,
        record.perpendicular_m,
        record.parallel_m,
    )

    if not all(math.isfinite(value) for value in values):
        raise ValueError("Baseline components must be finite.")

    if not math.isclose(
        record.total_m**2,
        record.parallel_m**2 + record.perpendicular_m**2,
        rel_tol=1e-10,
        abs_tol=1e-6,
    ):
        raise ValueError("Baseline decomposition validation failed.")

    with SessionLocal.begin() as session:
        product_names = {
            record.reference_product_name,
            record.secondary_product_name,
        }

        products = session.execute(
            select(
                SARProduct.id,
                SARProduct.product_name,
            ).where(SARProduct.product_name.in_(product_names))
        ).all()

        product_ids = {name: product_id for product_id, name in products}

        missing = product_names - product_ids.keys()

        if missing:
            raise ValueError(f"SAR products not registered: {sorted(missing)}")

        reference_id = product_ids[record.reference_product_name]

        secondary_id = product_ids[record.secondary_product_name]

        stmt = (
            insert(InSARBaseline)
            .values(
                run_id=run_id,
                reference_product_id=reference_id,
                secondary_product_id=secondary_id,
                catalogue_burst_id=record.catalogue_burst_id,
                total_m=record.total_m,
                perpendicular_m=record.perpendicular_m,
                parallel_m=record.parallel_m,
            )
            .on_conflict_do_nothing(constraint="uq_insar_baseline_run_pair_burst")
            .returning(InSARBaseline.id)
        )

        baseline_id = session.scalar(stmt)

        if baseline_id is None:
            baseline_id = session.scalar(
                select(InSARBaseline.id).where(
                    InSARBaseline.run_id == run_id,
                    InSARBaseline.reference_product_id == reference_id,
                    InSARBaseline.secondary_product_id == secondary_id,
                    InSARBaseline.catalogue_burst_id == record.catalogue_burst_id,
                )
            )

        if baseline_id is None:
            raise RuntimeError("Unable to register or retrieve InSAR baseline.")

    return baseline_id
