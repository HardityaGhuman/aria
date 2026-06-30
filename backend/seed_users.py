"""
backend/seed_users.py
---------------------
Provision the initial user accounts. There is no signup endpoint — accounts are
seeded here, which removes the self-assigned-HR security hole.

Run: ``python -m backend.seed_users``

Passwords come from environment variables, or an interactive prompt if unset —
never hardcoded in git. Idempotent: existing emails are skipped.
"""
import getpass
import os
import sys

from backend.core.auth import hash_password
from backend.core.users import (
    UserError,
    create_user,
    get_user_by_email,
    initialize_users_table,
)

# (email, role, region, env var holding the password)
SEED_USERS = [
    ("hr@gsvh.test", "hr", "us", "SEED_HR_PASSWORD"),
    ("manager@gsvh.test", "manager", "us", "SEED_MANAGER_PASSWORD"),
    ("employee@gsvh.test", "employee", "us", "SEED_EMPLOYEE_PASSWORD"),
    ("employee2@gsvh.test", "employee", "india", "SEED_EMPLOYEE2_PASSWORD"),
]


def _password_for(env_var: str, email: str) -> str | None:
    password = os.getenv(env_var)
    if password:
        return password
    try:
        return getpass.getpass(f"Password for {email} ({env_var} unset): ") or None
    except (EOFError, KeyboardInterrupt):
        return None


def main() -> int:
    initialize_users_table()
    created = skipped = 0
    for email, role, region, env_var in SEED_USERS:
        if get_user_by_email(email):
            print(f"skip   {email} (already exists)")
            skipped += 1
            continue
        password = _password_for(env_var, email)
        if not password:
            print(f"skip   {email} (no password provided)", file=sys.stderr)
            skipped += 1
            continue
        create_user(email, hash_password(password), role, region)
        print(f"create {email} ({role})")
        created += 1
    print(f"done: {created} created, {skipped} skipped")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except UserError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        sys.exit(1)