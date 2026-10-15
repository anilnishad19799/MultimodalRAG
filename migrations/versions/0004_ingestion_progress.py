from alembic import op
import sqlalchemy as sa


revision = "0004_ingestion_progress"
down_revision = "0003_memory_vector_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("progress_stage", sa.String(length=40), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("progress_message", sa.String(length=255), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("progress_details", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "progress_message")
    op.drop_column("ingestion_jobs", "progress_details")
    op.drop_column("ingestion_jobs", "progress_percent")
    op.drop_column("ingestion_jobs", "progress_total")
    op.drop_column("ingestion_jobs", "progress_current")
    op.drop_column("ingestion_jobs", "progress_stage")
