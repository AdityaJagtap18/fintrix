"""Manage the credentials workbook (api/auth.py) — add, update, remove, or list users
without ever typing a bcrypt hash by hand. Never prints, logs, or writes a plaintext
password anywhere except into the prompt you type it into (or, for --generate, into this
command's own stdout — copy it now, it's never shown again).

The workbook lives at data/credentials.xlsx by default (override with
CREDENTIALS_XLSX_PATH, same env var api/auth.py reads) — columns: username, role
("ap", "admin", or "superadmin"), password_hash, doc_quota, docs_used, valid_until,
created_at. Gitignored; never commit it.

Mainly useful for bootstrapping the very first superadmin account — after that, the
superadmin dashboard in the app itself is the normal way to create AP/admin demo users
with a quota and expiry. This script can still do all of it too.

Run with:
    PYTHONPATH=src .venv/bin/python scripts/manage_credentials.py add --username priya --role ap
    PYTHONPATH=src .venv/bin/python scripts/manage_credentials.py add --username demo1 --role ap \\
        --generate --quota 25 --valid-days 14
    PYTHONPATH=src .venv/bin/python scripts/manage_credentials.py list
    PYTHONPATH=src .venv/bin/python scripts/manage_credentials.py remove --username priya --role ap
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime, timedelta

from api.auth import (
    UserRecord,
    credentials_path,
    delete_user,
    generate_password,
    hash_password,
    load_users,
    upsert_user,
)

_ROLES = ["ap", "admin", "superadmin"]


def cmd_add(args: argparse.Namespace) -> None:
    if args.generate:
        password = generate_password()
    else:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if not password:
            print("Password cannot be empty.", file=sys.stderr)
            sys.exit(1)
        if password != confirm:
            print("Passwords did not match.", file=sys.stderr)
            sys.exit(1)

    existing = load_users().get(args.username)
    today = datetime.now(UTC).date()

    # --quota/--valid-days left unset on an *existing* user means "leave it alone" (e.g.
    # a password-only reset shouldn't silently wipe a quota or expiry back to unlimited/
    # never) — only an explicit flag changes them. A brand-new user with no flags gets
    # the old defaults: unlimited, no expiry.
    if args.valid_days is not None:
        valid_until = today + timedelta(days=args.valid_days)
    else:
        valid_until = existing.valid_until if existing else None
    doc_quota = args.quota if args.quota is not None else (existing.doc_quota if existing else None)
    created_at = existing.created_at if existing else today

    upsert_user(
        UserRecord(
            username=args.username,
            role=args.role,
            password_hash=hash_password(password),
            doc_quota=doc_quota,
            docs_used=existing.docs_used if existing else 0,
            valid_until=valid_until,
            created_at=created_at,
        )
    )

    verb = "Updated" if existing else "Added"
    print(f"{verb} credentials for {args.username!r} ({args.role}) in {credentials_path()}")
    if args.generate:
        print(f"Generated password: {password}")
    if args.quota is not None:
        print(f"Document quota: {args.quota}")
    if valid_until:
        print(f"Valid until: {valid_until.isoformat()}")


def cmd_list(_args: argparse.Namespace) -> None:
    path = credentials_path()
    users = load_users()
    if not users:
        print(f"{path} has no users yet.")
        return
    today = datetime.now(UTC).date()
    for user in sorted(users.values(), key=lambda u: u.username):
        quota = f"{user.docs_used}/{user.doc_quota}" if user.doc_quota is not None else "unlimited"
        expiry = user.valid_until.isoformat() if user.valid_until else "never"
        expired = " (EXPIRED)" if user.valid_until and user.valid_until < today else ""
        print(f"{user.username} ({user.role}) — docs {quota}, valid until {expiry}{expired}")


def cmd_remove(args: argparse.Namespace) -> None:
    path = credentials_path()
    record = load_users().get(args.username)
    if record is None or record.role != args.role:
        print(f"No matching user {args.username!r} ({args.role}) in {path}")
        return
    delete_user(args.username)
    print(f"Removed {args.username!r} ({args.role}) from {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser(
        "add", help="Add a new user or update an existing one's password"
    )
    add_parser.add_argument("--username", required=True)
    add_parser.add_argument("--role", required=True, choices=_ROLES)
    add_parser.add_argument(
        "--generate", action="store_true", help="Generate a password instead of prompting for one"
    )
    add_parser.add_argument(
        "--quota",
        type=int,
        default=None,
        help="Max documents this account may extract (omit: unchanged, or unlimited if new)",
    )
    add_parser.add_argument(
        "--valid-days",
        type=int,
        default=None,
        help="Account expires this many days from now (omit: unchanged, or never if new)",
    )
    add_parser.set_defaults(func=cmd_add)

    list_parser = subparsers.add_parser(
        "list", help="List every user with role, quota usage, and expiry (never passwords/hashes)"
    )
    list_parser.set_defaults(func=cmd_list)

    remove_parser = subparsers.add_parser("remove", help="Remove a user")
    remove_parser.add_argument("--username", required=True)
    remove_parser.add_argument("--role", required=True, choices=_ROLES)
    remove_parser.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
