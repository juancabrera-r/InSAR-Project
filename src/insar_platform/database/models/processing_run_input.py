import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from insar_platform.database.base import Base

if TYPE_CHECKING:
    from insar_platform.database.models.processing_run import ProcessingRun
    from insar_platform.database.models.sar_product import SARProduct


class ProcessingRunInput(Base):
    __tablename__ = "processing_run_input"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processing_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sar_product.id", ondelete="RESTRICT"),
        primary_key=True,
    )

    run: Mapped["ProcessingRun"] = relationship(
        back_populates="inputs",
    )

    product: Mapped["SARProduct"] = relationship(
        back_populates="run_inputs",
    )
