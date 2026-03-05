"""phase2_gear_profile_and_trip_dates

Revision ID: dcc6439ee150
Revises: 5d6c6ab024b5
Create Date: 2026-03-05 09:52:57.384628

Changes:
- Create gear_profiles table (photographer's gear vault per staff user)
- Add trips.start_date, trips.end_date (Date columns, nullable)
- Add trips.gear_profile_id (FK to gear_profiles, nullable)
- Make trips.duration nullable (new trips use start/end dates instead)

SQLite note: All modifications to the trips table are batched into a single
batch_alter_table block (recreate='auto'). SQLite does not support ALTER COLUMN
or inline FK constraints, so Alembic's batch mode handles this via table
recreation. PostgreSQL uses the same path and is unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dcc6439ee150'
down_revision: Union[str, None] = '5d6c6ab024b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create gear_profiles table ────────────────────────────────────────────
    op.create_table(
        'gear_profiles',
        sa.Column('id',            sa.Integer(),      nullable=False),
        sa.Column('staff_user_id', sa.Integer(),      nullable=False),
        sa.Column('name',          sa.String(100),    nullable=False),
        sa.Column('camera_type',   sa.String(50),     nullable=False),
        sa.Column('lenses',        sa.Text(),          nullable=True),
        sa.Column('has_tripod',    sa.Boolean(),       nullable=False),
        sa.Column('has_filters',   sa.Text(),          nullable=True),
        sa.Column('has_gimbal',    sa.Boolean(),       nullable=False),
        sa.Column('notes',         sa.Text(),          nullable=True),
        sa.Column('created_at',    sa.DateTime(),      nullable=False),
        sa.Column('updated_at',    sa.DateTime(),      nullable=False),
        sa.ForeignKeyConstraint(['staff_user_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_gear_profiles_staff_user_id'),
        'gear_profiles', ['staff_user_id'], unique=False,
    )

    # ── Modify trips table (all changes in one batch for SQLite compat) ───────
    # batch_alter_table with recreate='auto' handles SQLite's lack of support
    # for ALTER COLUMN and inline foreign key additions. PostgreSQL takes the
    # same code path without issue.
    with op.batch_alter_table('trips', recreate='auto') as batch_op:
        batch_op.add_column(sa.Column('gear_profile_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('start_date',      sa.Date(),    nullable=True))
        batch_op.add_column(sa.Column('end_date',        sa.Date(),    nullable=True))
        batch_op.alter_column(
            'duration',
            existing_type=sa.INTEGER(),
            nullable=True,
        )
        batch_op.create_index(
            op.f('ix_trips_gear_profile_id'),
            ['gear_profile_id'], unique=False,
        )
        batch_op.create_foreign_key(
            'fk_trips_gear_profile_id',
            'gear_profiles',
            ['gear_profile_id'],
            ['id'],
        )


def downgrade() -> None:
    # ── Remove trips modifications ────────────────────────────────────────────
    with op.batch_alter_table('trips', recreate='auto') as batch_op:
        batch_op.drop_constraint('fk_trips_gear_profile_id', type_='foreignkey')
        batch_op.drop_index(op.f('ix_trips_gear_profile_id'))
        batch_op.alter_column(
            'duration',
            existing_type=sa.INTEGER(),
            nullable=False,
        )
        batch_op.drop_column('end_date')
        batch_op.drop_column('start_date')
        batch_op.drop_column('gear_profile_id')

    # ── Drop gear_profiles table ──────────────────────────────────────────────
    op.drop_index(op.f('ix_gear_profiles_staff_user_id'), table_name='gear_profiles')
    op.drop_table('gear_profiles')
