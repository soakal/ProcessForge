"""CLI to manage LLM provider API keys in the OS keyring (llm/client.py's fallback store).

Usage:
    python -m llm.secrets set <anthropic|openrouter>
    python -m llm.secrets status
    python -m llm.secrets delete <anthropic|openrouter>
"""
from __future__ import annotations

import argparse
import getpass
import sys

import keyring
import keyring.errors

_SERVICE = "processforge"
_VALID_PROVIDERS = ("anthropic", "openrouter")


def _username(provider: str) -> str:
    return f"llm_api_key_{provider}"


def _cmd_set(provider: str) -> int:
    if provider not in _VALID_PROVIDERS:
        print(f"error: invalid provider {provider!r} (must be one of: {', '.join(_VALID_PROVIDERS)})", file=sys.stderr)
        return 1
    api_key = getpass.getpass(f"Enter API key for {provider}: ")
    keyring.set_password(_SERVICE, _username(provider), api_key)
    print(f"{provider}: stored")
    return 0


def _cmd_status() -> int:
    for provider in _VALID_PROVIDERS:
        present = bool(keyring.get_password(_SERVICE, _username(provider)))
        print(f"{provider}: {'yes' if present else 'no'}")
    return 0


def _cmd_delete(provider: str) -> int:
    if provider not in _VALID_PROVIDERS:
        print(f"error: invalid provider {provider!r} (must be one of: {', '.join(_VALID_PROVIDERS)})", file=sys.stderr)
        return 1
    try:
        keyring.delete_password(_SERVICE, _username(provider))
    except keyring.errors.PasswordDeleteError:
        print(f"{provider}: nothing stored", file=sys.stderr)
        return 1
    print(f"{provider}: deleted")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m llm.secrets", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Store an API key for a provider")
    set_parser.add_argument("provider")

    subparsers.add_parser("status", help="Report which providers have a stored key")

    delete_parser = subparsers.add_parser("delete", help="Remove a stored API key for a provider")
    delete_parser.add_argument("provider")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "set":
        return _cmd_set(args.provider)
    if args.command == "status":
        return _cmd_status()
    if args.command == "delete":
        return _cmd_delete(args.provider)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
