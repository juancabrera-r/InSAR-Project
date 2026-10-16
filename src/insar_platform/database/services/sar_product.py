from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert

from insar_platform.database.connection import SessionLocal
from insar_platform.database.models.sar_product import SARProduct


@dataclass(frozen=True)
class SARProductRecord:
    product_name: str
    satellite: str
    acquisition_time: datetime
    relative_orbit: int | None = None


def register_sar_products(
    products: Iterable[SARProductRecord],
) -> int:
    unique_products = {product.product_name: product for product in products}

    if not unique_products:
        return 0

    values = [
        {
            "product_name": product.product_name,
            "satellite": product.satellite,
            "acquisition_time": product.acquisition_time,
            "relative_orbit": product.relative_orbit,
        }
        for product in unique_products.values()
    ]

    with SessionLocal.begin() as session:
        stmt = (
            insert(SARProduct)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[SARProduct.product_name],
            )
            .returning(SARProduct.id)
        )

        inserted_ids = session.scalars(stmt).all()

    return len(inserted_ids)
