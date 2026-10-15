from alembic import op
import sqlalchemy as sa


revision = "0003_memory_vector_sync"
down_revision = "0002_conversation_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "long_term_memories",
        sa.Column("vector_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_long_term_memories_vector_synced", "long_term_memories", ["vector_synced"])


def downgrade() -> None:
    op.drop_index("ix_long_term_memories_vector_synced", table_name="long_term_memories")
    op.drop_column("long_term_memories", "vector_synced")
