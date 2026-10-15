from alembic import op
import sqlalchemy as sa


revision = "0002_conversation_memory"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_message_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_threads_owner_id", "conversation_threads", ["owner_id"])
    op.create_index("ix_conversation_threads_updated_at", "conversation_threads", ["updated_at"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("thread_id", sa.String(length=36), sa.ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_messages_thread_id", "conversation_messages", ["thread_id"])
    op.create_index("ix_conversation_messages_created_at", "conversation_messages", ["created_at"])
    op.create_table(
        "long_term_memories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("source_thread_id", sa.String(length=36), sa.ForeignKey("conversation_threads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_message_id", sa.String(length=36), nullable=True),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "fingerprint", name="uq_memory_owner_fingerprint"),
    )
    op.create_index("ix_long_term_memories_owner_id", "long_term_memories", ["owner_id"])
    op.create_index("ix_long_term_memories_source_thread_id", "long_term_memories", ["source_thread_id"])


def downgrade() -> None:
    op.drop_table("long_term_memories")
    op.drop_table("conversation_messages")
    op.drop_table("conversation_threads")
