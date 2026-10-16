import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from insar_platform.database.base import Base

if TYPE_CHECKING:
    from insar_platform.database.models.processing_run import ProcessingRun
    from insar_platform.database.models.sar_product import SARProduct


class SARFileType(StrEnum):
    MEASUREMENT = "measurement"
    ANNOTATION = "annotation"


class SARProductFile(Base):
    __tablename__ = "sar_product_file"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sar_product.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    download_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processing_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    file_type: Mapped[SARFileType] = mapped_column(
        Enum(
            SARFileType,
            name="sar_file_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    file_path: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    file_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    product: Mapped["SARProduct"] = relationship(
        back_populates="files",
    )

    download_run: Mapped["ProcessingRun | None"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "file_type",
            "file_path",
            name="uq_sar_product_file",
        ),
    )
