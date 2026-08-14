"""Promote an existing account to platform superuser.

    python -m scripts.create_superuser --email ops@example.com

Superusers reach the /admin endpoints (model prices, credit adjustments) and can
read any organisation. Grant it to as few accounts as possible.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from core.db import dispose_engine, session_scope
from core.models import User


async def promote(email: str, revoke: bool) -> None:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.email == email.lower()))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"no account with email {email}; register it first")
        user.is_superuser = not revoke
        # Force existing sessions to pick up the change on their next refresh.
        user.token_epoch += 1
        print(f"{'revoked' if revoke else 'granted'} superuser for {email}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()

    async def _run() -> None:
        try:
            await promote(args.email, args.revoke)
        finally:
            await dispose_engine()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
