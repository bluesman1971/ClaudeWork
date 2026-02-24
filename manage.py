"""
manage.py — CLI admin commands for Trip Master (replaces `flask create-user`).

Usage:
    python manage.py create-user
    python manage.py create-user --email admin@example.com --name "Admin" --role admin
"""

import click
import bcrypt

from database import SessionLocal
from models import StaffUser


@click.command('create-user')
@click.option('--email',    prompt=True,  help='Staff email address')
@click.option('--name',     prompt=True,  help='Full name')
@click.option('--password', prompt=True,  hide_input=True, confirmation_prompt=True,
              help='Login password (hidden)')
@click.option('--role',     default='staff', type=click.Choice(['admin', 'staff']),
              show_default=True, help='Account role')
def create_user(email: str, name: str, password: str, role: str):
    """Create a new staff account."""
    email = email.strip().lower()
    name  = name.strip()

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)).decode()

    with SessionLocal() as session:
        existing = session.query(StaffUser).filter_by(email=email).first()
        if existing:
            click.echo(f'✗ An account with email {email!r} already exists (id={existing.id}).', err=True)
            raise SystemExit(1)

        user = StaffUser(
            email         = email,
            full_name     = name,
            password_hash = pw_hash,
            role          = role,
            is_active     = True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        click.echo(f'✓ Created {role} account for {email!r} (id={user.id})')


if __name__ == '__main__':
    create_user()
