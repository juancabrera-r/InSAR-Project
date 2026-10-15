import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from insar_platform.database.base import Base


class SARProduct(Base):
    __tablename__ = "sar_product"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    product_name: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
    )

    satellite: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    acquisition_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    relative_orbit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    footprint = mapped_column(
        Geometry(
            geometry_type="MULTIPOLYGON",
            srid=4326,
            spatial_index=True,
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
