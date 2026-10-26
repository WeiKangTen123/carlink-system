"""Account commands for the server, where there is no dashboard admin yet
(the first account) or none can sign in (a forgotten admin password).

    docker exec -it carlink_api python -m app.auth.manage create-user --email you@example.com --name "Your Name" --role admin [--username you]
    docker exec -it carlink_api python -m app.auth.manage reset-password --email you@example.com
    docker exec carlink_api python -m app.auth.manage list-users

The password is asked for twice, or read from stdin with --password-stdin
(e.g. `... --password-stdin < file`) so it never appears in the process
list or shell history.
"""
import argparse
import getpass
import sys

from app.auth.core import (
    ROLES,
    audit,
    end_user_sessions,
    normalize_email,
    normalize_username,
    username_from_email,
    username_problem,
    username_taken,
)
from app.auth.passwords import hash_password, password_problem
from app.reports.db import SessionLocal, init_db
from app.reports.models import User


def _read_password(from_stdin: bool, personal: tuple[str, ...] = ()) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Again: ") != password:
            sys.exit("Passwords don't match.")
    problem = password_problem(password, personal)
    if problem:
        sys.exit(problem)
    return password


def create_user(args) -> None:
    db = SessionLocal()
    try:
        email = normalize_email(args.email)
        if db.query(User).filter(User.email == email).first():
            sys.exit(f"There is already an account for {email}.")
        if args.username:
            username = normalize_username(args.username)
            problem = username_problem(username)
            if problem:
                sys.exit(problem)
            if username_taken(db, username):
                sys.exit(f"The username {username} is taken.")
        else:
            username = username_from_email(db, email)
        user = User(
            email=email,
            username=username,
            name=args.name.strip(),
            role=args.role,
            password_hash=hash_password(_read_password(args.password_stdin, (args.name, email))),
        )
        db.add(user)
        audit(db, None, "user_created", detail=f"{email} ({args.role}) from the server command line")
        db.commit()
        print(f"Created {args.role} {email} (username {username}).")
    finally:
        db.close()


def reset_password(args) -> None:
    db = SessionLocal()
    try:
        email = normalize_email(args.email)
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            sys.exit(f"No account for {email}.")
        user.password_hash = hash_password(_read_password(args.password_stdin, (user.name, user.email)))
        user.must_change_password = False
        user.failed_logins = 0
        user.locked_until = None
        user.active = True
        end_user_sessions(db, user.id)
        audit(db, None, "password_reset", detail=f"{email} from the server command line")
        db.commit()
        print(f"Password reset for {email}; the account is unlocked and enabled.")
    finally:
        db.close()


def list_users(_args) -> None:
    db = SessionLocal()
    try:
        for u in db.query(User).order_by(User.username, User.name).all():
            state = "active" if u.active else "disabled"
            print(f"{u.email:40} {(u.username or ''):24} {u.role:9} {state:9} {u.name}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.auth.manage")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", choices=ROLES, default="user")
    create.add_argument("--username", help="default: made from the email")
    create.add_argument("--password-stdin", action="store_true")
    create.set_defaults(run=create_user)

    reset = sub.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password-stdin", action="store_true")
    reset.set_defaults(run=reset_password)

    sub.add_parser("list-users").set_defaults(run=list_users)

    args = parser.parse_args()
    init_db()
    args.run(args)


if __name__ == "__main__":
    main()
