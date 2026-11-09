"""Initial schema (user, meeting, task, chat_message).

Revision ID: 0001
Revises: 
Create Date: 2026-11-08 11:57:07.755363

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_email'), ['email'], unique=True)

    op.create_table('meeting',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('meeting_date', sa.DateTime(), nullable=True),
    sa.Column('audio_filename', sa.String(length=255), nullable=True),
    sa.Column('transcript', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('decisions_json', sa.Text(), nullable=True),
    sa.Column('topics_json', sa.Text(), nullable=True),
    sa.Column('ai_status', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('meeting', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_meeting_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_user_id'), ['user_id'], unique=False)

    op.create_table('chat_message',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('meeting_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("role IN ('user', 'assistant')", name='ck_chat_message_role'),
    sa.ForeignKeyConstraint(['meeting_id'], ['meeting.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('chat_message', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_message_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_message_user_id'), ['user_id'], unique=False)

    op.create_table('task',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('meeting_id', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=700), nullable=False),
    sa.Column('assignee_name', sa.String(length=150), nullable=True),
    sa.Column('deadline', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=True),
    sa.Column('reminder_sent', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("status IN ('Pending', 'In Progress', 'Completed')", name='ck_task_status'),
    sa.ForeignKeyConstraint(['meeting_id'], ['meeting.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('task', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_task_status'), ['status'], unique=False)



def downgrade():
    with op.batch_alter_table('task', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_status'))
        batch_op.drop_index(batch_op.f('ix_task_meeting_id'))

    op.drop_table('task')
    with op.batch_alter_table('chat_message', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_chat_message_user_id'))
        batch_op.drop_index(batch_op.f('ix_chat_message_meeting_id'))

    op.drop_table('chat_message')
    with op.batch_alter_table('meeting', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_meeting_user_id'))
        batch_op.drop_index(batch_op.f('ix_meeting_created_at'))

    op.drop_table('meeting')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_email'))

    op.drop_table('user')
