import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from insar_platform.database.connection import SessionLocal
from insar_platform.database.models.sar_product import SARProduct
from insar_platform.database.models.sar_product_file import (
    SARFileType,
    SARProductFile,
)


@dataclass(frozen=True)
class SARProductFileRecord:
    product_name: str
    file_type: SARFileType
    file_path: Path
    metadata_json: dict[str, Any] | None = None


def register_sar_product_files(
    records: Iterable[SARProductFileRecord],
    download_run_id: uuid.UUID | None = None,
) -> int:
    """
    Register local SAR product files.

    Returns the number of newly inserted records.

    Existing records are preserved, including their
    original download_run_id.
    """
    records = list(records)

    if not records:
        return 0

    product_names = {record.product_name for record in records}

    with SessionLocal.begin() as session:
        products = session.execute(
            select(
                SARProduct.id,
                SARProduct.product_name,
            ).where(SARProduct.product_name.in_(product_names))
        ).all()

        product_ids = {
            product_name: product_id for product_id, product_name in products
        }

        missing = product_names - product_ids.keys()

        if missing:
            raise ValueError(f"SAR products not registered: {sorted(missing)}")

        values = []
        seen = set()

        for record in records:
            path = record.file_path.resolve()

            if not path.is_file():
                raise FileNotFoundError(f"SAR product file not found: {path}")

            file_size = path.stat().st_size

            if file_size <= 0:
                raise ValueError(f"SAR product file is empty: {path}")

            product_id = product_ids[record.product_name]

            key = (
                product_id,
                record.file_type,
                str(path),
            )

            if key in seen:
                continue

            seen.add(key)

            values.append(
                {
                    "product_id": product_id,
                    "download_run_id": download_run_id,
                    "file_type": record.file_type,
                    "file_path": str(path),
                    "file_size_bytes": file_size,
                    "metadata_json": record.metadata_json,
                }
            )

        if not values:
            return 0

        stmt = (
            insert(SARProductFile)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_sar_product_file")
            .returning(SARProductFile.id)
        )

        inserted_ids = session.scalars(stmt).all()

    return len(inserted_ids)
