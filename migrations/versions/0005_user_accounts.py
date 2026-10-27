from alembic import op
import sqlalchemy as sa


revision = "0005_user_accounts"
down_revision = "0004_ingestion_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_documents_checksum", table_name="documents")
    op.create_index("ix_documents_checksum", "documents", ["checksum"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.add_column("documents", sa.Column("owner_id", sa.String(36), nullable=True))
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.execute("UPDATE documents SET owner_id = 'legacy' WHERE owner_id IS NULL")
    op.alter_column("documents", "owner_id", nullable=False, server_default="legacy")


def downgrade() -> None:
    op.drop_index("ix_documents_checksum", table_name="documents")
    op.create_index("ix_documents_checksum", "documents", ["checksum"], unique=True)
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_column("documents", "owner_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
