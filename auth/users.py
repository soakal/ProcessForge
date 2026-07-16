"""CLI to manage ProcessForge operators (auth/repository.py's AuthRepository).

Usage:
    python -m auth.users create <username>
    python -m auth.users list
    python -m auth.users delete <username>
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from auth.repository import AuthRepository, DuplicateOperatorError, OperatorNotFoundError

_MIN_PASSWORD_LENGTH = 8


def _db_path() -> str:
    return os.environ.get("PROCESSFORGE_DB_PATH", "./kb/processforge.db")


def _cmd_create(username: str) -> int:
    password = getpass.getpass(f"Enter password for {username}: ")
    if not password.strip():
        print("error: no password entered (empty input); operator not created", file=sys.stderr)
        return 1
    if len(password.strip()) < _MIN_PASSWORD_LENGTH:
        print(f"error: password must be at least {_MIN_PASSWORD_LENGTH} characters; operator not created", file=sys.stderr)
        return 1

    repo = AuthRepository(_db_path())
    try:
        repo.create_operator(username, password)
    except DuplicateOperatorError:
        print(f"error: operator username already exists: {username!r}", file=sys.stderr)
        return 1
    finally:
        repo.close()
    print(f"{username}: created")
    return 0


def _cmd_list() -> int:
    repo = AuthRepository(_db_path())
    try:
        operators = repo.list_operators()
    finally:
        repo.close()
    for operator in operators:
        print(f"{operator['username']}\t{operator['created_at']}")
    return 0


def _cmd_delete(username: str) -> int:
    repo = AuthRepository(_db_path())
    try:
        repo.delete_operator(username)
    except OperatorNotFoundError:
        print(f"error: operator username not found: {username!r}", file=sys.stderr)
        return 1
    finally:
        repo.close()
    print(f"{username}: deleted")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m auth.users", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a new operator")
    create_parser.add_argument("username")

    subparsers.add_parser("list", help="List all operators")

    delete_parser = subparsers.add_parser("delete", help="Delete an operator")
    delete_parser.add_argument("username")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "create":
        return _cmd_create(args.username)
    if args.command == "list":
        return _cmd_list()
    if args.command == "delete":
        return _cmd_delete(args.username)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
