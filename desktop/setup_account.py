"""GUI-free account creation for the desktop operator setup flow.

create_account() is the single entry point that does all the real work — pure
Python, no tkinter, no dotenv loading — safe to import and call directly from
tests. Reuses the exact production migration path (pipeline._migrate) and the
same AuthRepository.create_operator() the CLI (auth/users.py) and API
(api/main.py) both call, so a desktop-created account is indistinguishable
from one created any other way.

`python -m desktop.setup_account` (or running this file directly) launches a
thin tkinter form on top of create_account() — that UI code is gated under
`if __name__ == "__main__":` so importing this module never opens a window or
loads .env.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from auth.repository import AuthRepository
from auth.users import _MIN_PASSWORD_LENGTH
from pipeline import _migrate


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile extracts to a temp dir at runtime, so
        # Path(__file__) would resolve inside that temp dir. Use the
        # directory containing the actual .exe instead.
        return Path(sys.executable).resolve().parent
    # desktop/setup_account.py -> desktop -> project root
    return Path(__file__).resolve().parent.parent


class AccountValidationError(ValueError):
    """Raised by create_account() when username/password input fails local
    validation (empty username, empty password, or password != confirm) —
    before any DB access happens."""


def _validate(username: str, password: str, confirm: str) -> str:
    """Shared local validation for create_account/update_password. Returns the
    stripped username. Raises AccountValidationError (before any DB access) on
    an empty username, an empty password, a password shorter than
    auth.users._MIN_PASSWORD_LENGTH, or a password/confirm mismatch."""
    username = username.strip()
    if not username:
        raise AccountValidationError("username must not be empty")
    if not password:
        raise AccountValidationError("password must not be empty")
    if len(password.strip()) < _MIN_PASSWORD_LENGTH:
        raise AccountValidationError(
            f"password must be at least {_MIN_PASSWORD_LENGTH} characters"
        )
    if password != confirm:
        raise AccountValidationError("password and confirm password do not match")
    return username


def create_account(username: str, password: str, confirm: str, db_path: str) -> str:
    """Validate + create a new operator account.

    Mirrors auth/users.py's _cmd_create: migrate then AuthRepository.create_operator().
    Raises AccountValidationError on invalid input (see _validate), and lets
    auth.repository.DuplicateOperatorError propagate unchanged if the username
    already exists. Returns the new operator's id on success.
    """
    username = _validate(username, password, confirm)
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        return repo.create_operator(username, password)
    finally:
        repo.close()


def update_password(username: str, password: str, confirm: str, db_path: str) -> None:
    """Validate + set a new password for an EXISTING operator.

    Same local validation as create_account (see _validate). Lets
    auth.repository.OperatorNotFoundError propagate unchanged if the username
    doesn't exist. Existing auth tokens for the operator are revoked by
    AuthRepository.set_password() so old sessions can't outlive the change.
    """
    username = _validate(username, password, confirm)
    _migrate(db_path)
    repo = AuthRepository(db_path)
    try:
        repo.set_password(username, password)
    finally:
        repo.close()


def main() -> int:
    """Thin tkinter wrapper around create_account(). Only imported/executed
    when this module is run directly — never at import time."""
    import tkinter as tk
    from tkinter import messagebox

    from dotenv import load_dotenv

    root = _project_root()
    load_dotenv(dotenv_path=root / ".env")
    db_path = os.environ.get("PROCESSFORGE_DB_PATH", str(root / "kb" / "processforge.db"))

    root = tk.Tk()
    root.title("ProcessForge — Operator Account")

    window_width, window_height = 340, 190
    x = (root.winfo_screenwidth() - window_width) // 2
    y = (root.winfo_screenheight() - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.lift()
    root.attributes("-topmost", True)
    root.after_idle(lambda: root.attributes("-topmost", False))
    root.focus_force()

    tk.Label(root, text="Username").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    username_entry = tk.Entry(root)
    username_entry.grid(row=0, column=1, padx=5, pady=5)

    tk.Label(root, text="Password").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    password_entry = tk.Entry(root, show="*")
    password_entry.grid(row=1, column=1, padx=5, pady=5)

    tk.Label(root, text="Confirm password").grid(row=2, column=0, sticky="e", padx=5, pady=5)
    confirm_entry = tk.Entry(root, show="*")
    confirm_entry.grid(row=2, column=1, padx=5, pady=5)

    def on_submit() -> None:
        try:
            create_account(
                username_entry.get(),
                password_entry.get(),
                confirm_entry.get(),
                db_path,
            )
        except Exception as exc:  # AccountValidationError or DuplicateOperatorError
            messagebox.showerror("Account not created", str(exc))
            return
        messagebox.showinfo("Account created", f"{username_entry.get().strip()}: created")
        root.destroy()

    def on_update() -> None:
        try:
            update_password(
                username_entry.get(),
                password_entry.get(),
                confirm_entry.get(),
                db_path,
            )
        except Exception as exc:  # AccountValidationError or OperatorNotFoundError
            messagebox.showerror("Password not updated", str(exc))
            return
        messagebox.showinfo("Password updated", f"{username_entry.get().strip()}: password updated")
        root.destroy()

    tk.Button(root, text="Create account", command=on_submit).grid(
        row=3, column=0, pady=10, padx=5, sticky="e"
    )
    tk.Button(root, text="Update password", command=on_update).grid(
        row=3, column=1, pady=10, padx=5, sticky="w"
    )

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
