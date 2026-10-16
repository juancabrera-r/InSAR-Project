import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from insar_platform.database.connection import SessionLocal
from insar_platform.database.models.processing_run_input import (
    ProcessingRunInput,
)
from insar_platform.database.models.sar_product import SARProduct


def register_processing_run_inputs(
    run_id: uuid.UUID,
    product_names: Iterable[str],
) -> int:
    names = set(product_names)

    if not names:
        return 0

    with SessionLocal.begin() as session:
        products = session.execute(
            select(
                SARProduct.id,
                SARProduct.product_name,
            ).where(SARProduct.product_name.in_(names))
        ).all()

        found_names = {name for _, name in products}
        missing = names - found_names

        if missing:
            raise ValueError(f"SAR products not registered: {sorted(missing)}")

        stmt = (
            insert(ProcessingRunInput)
            .values(
                [
                    {
                        "run_id": run_id,
                        "product_id": product_id,
                    }
                    for product_id, _ in products
                ]
            )
            .on_conflict_do_nothing()
            .returning(ProcessingRunInput.product_id)
        )

        inserted_ids = session.scalars(stmt).all()

    return len(inserted_ids)
