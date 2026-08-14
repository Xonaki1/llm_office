"""Re-wrap stored provider credentials under the newest master key.

Rotation procedure:

  1. Generate a new KEK:  python -m scripts.rotate_keys --generate
  2. Add it to MASTER_KEYS alongside the existing one and set
     MASTER_KEY_VERSION to the new version. Keep the old key present — rows
     encrypted under it stay readable until this script has re-wrapped them.
  3. Restart the services, then run: python -m scripts.rotate_keys --apply
  4. Once `--status` reports nothing outstanding, remove the old key.

The plaintext of a secret exists only inside a single `rewrap` call and is never
written anywhere.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from core.config import get_settings
from core.crypto import generate_master_key, needs_rewrap, rewrap
from core.db import dispose_engine, session_scope
from core.models import ApiKey


async def status() -> None:
    version, _ = get_settings().active_master_key
    async with session_scope() as session:
        rows = (await session.execute(select(ApiKey))).scalars().all()
        outstanding = [row for row in rows if needs_rewrap(row.ciphertext)]
    print(f"active master key version: {version}")
    print(f"stored credentials: {len(rows)}")
    print(f"awaiting re-wrap:   {len(outstanding)}")
    for row in outstanding:
        print(f"  - {row.id} org={row.org_id} provider={row.provider}")


async def apply() -> None:
    rotated = failed = 0
    async with session_scope() as session:
        rows = (await session.execute(select(ApiKey))).scalars().all()
        for row in rows:
            if not needs_rewrap(row.ciphertext):
                continue
            try:
                row.ciphertext = rewrap(row.ciphertext, aad=row.org_id)
                rotated += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the rest
                failed += 1
                print(f"FAILED {row.id} (org {row.org_id}): {exc}")
    print(f"re-wrapped {rotated} credential(s); {failed} failure(s)")
    if failed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="print a new base64 KEK")
    group.add_argument("--status", action="store_true", help="report what needs re-wrapping")
    group.add_argument("--apply", action="store_true", help="re-wrap under the active KEK")
    args = parser.parse_args()

    if args.generate:
        print(generate_master_key())
        return

    async def _run() -> None:
        try:
            await (status() if args.status else apply())
        finally:
            await dispose_engine()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
