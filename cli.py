"""Command-line user management.

    flask --app app create-user USERNAME
    flask --app app set-password USERNAME

Both prompt for the password without echoing it, so it never ends up
in the shell history. set-password also unlocks the "owner" account
that the multi-user migration creates for existing data.
"""

import click
from flask.cli import with_appcontext
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import User
from routes.auth import create_user, normalize_username, validate_password


password_option = click.password_option(
    "--password",
    prompt="Password",
    confirmation_prompt=True,
    help="Prompted for, hidden, if not given.",
)


@click.command("create-user")
@with_appcontext
@click.argument("username")
@password_option
def create_user_command(username, password):
    """Create a user who can sign in."""
    try:
        user = create_user(username, password)
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        message = "That username is taken." if isinstance(error, IntegrityError) else str(error)
        raise click.ClickException(message) from None

    click.echo(f"Created user {user.username}.")


@click.command("set-password")
@with_appcontext
@click.argument("username")
@password_option
def set_password_command(username, password):
    """Set a user's password. Their other sessions are signed out."""
    user = User.query.filter_by(username=normalize_username(username)).first()

    if user is None:
        raise click.ClickException(f"No user named {normalize_username(username)!r}.")

    try:
        validate_password(password)
    except ValueError as error:
        raise click.ClickException(str(error)) from None

    user.set_password(password)
    db.session.commit()
    click.echo(f"Password set for {user.username}.")


def register_commands(app):
    app.cli.add_command(create_user_command)
    app.cli.add_command(set_password_command)
