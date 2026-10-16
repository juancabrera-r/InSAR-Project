import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from insar_platform.database.base import Base

if TYPE_CHECKING:
    from insar_platform.database.models.processing_run import ProcessingRun
    from insar_platform.database.models.sar_product import SARProduct


class InSARBaseline(Base):
    __tablename__ = "insar_baseline"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processing_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    reference_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sar_product.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    secondary_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sar_product.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    catalogue_burst_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    total_m: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    perpendicular_m: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    parallel_m: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    run: Mapped["ProcessingRun"] = relationship()

    reference_product: Mapped["SARProduct"] = relationship(
        foreign_keys=[reference_product_id],
    )

    secondary_product: Mapped["SARProduct"] = relationship(
        foreign_keys=[secondary_product_id],
    )

    __table_args__ = (
        CheckConstraint(
            "reference_product_id <> secondary_product_id",
            name="ck_insar_baseline_distinct_products",
        ),
        UniqueConstraint(
            "run_id",
            "reference_product_id",
            "secondary_product_id",
            "catalogue_burst_id",
            name="uq_insar_baseline_run_pair_burst",
        ),
    )
