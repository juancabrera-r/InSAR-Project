"""Initial InSAR schema.

Revision ID: c3fb036fcbf3
Revises:
"""

import sqlalchemy as sa
from geoalchemy2 import Geometry

from alembic import op

revision = "c3fb036fcbf3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sar_product",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("product_name", sa.String(), nullable=False),
        sa.Column("satellite", sa.String(), nullable=False),
        sa.Column(
            "acquisition_time",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("relative_orbit", sa.Integer(), nullable=True),
        sa.Column(
            "footprint",
            Geometry(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                dimension=2,
                spatial_index=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_name"),
    )


def downgrade() -> None:
    op.drop_table("sar_product")
