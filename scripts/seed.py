"""Seed a demo organisation with a working multi-provider team.

    python -m scripts.seed --email you@example.com --password '<12+ chars>'

Idempotent: re-running updates nothing and prints the existing ids.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets

from sqlalchemy import select

from core import billing
from core.db import dispose_engine, session_scope
from core.models import Agent, Membership, Org, User, Workflow
from core.security import hash_password


async def seed(email: str, password: str, org_name: str, credits_cents: int) -> None:
    async with session_scope() as session:
        email = email.lower()
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            user = User(email=email, password_hash=hash_password(password), name="Owner")
            session.add(user)
            await session.flush()
            print(f"created user {user.id} ({email})")
        else:
            print(f"user {user.id} already exists")

        membership = (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id)
            )
        ).scalar_one_or_none()

        if membership is None:
            org = Org(name=org_name, slug=f"demo-{secrets.token_hex(3)}", key_mode="managed")
            session.add(org)
            await session.flush()
            session.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
            await session.flush()
            await billing.grant(
                session,
                org_id=org.id,
                amount_cents=credits_cents,
                kind="bonus",
                description="seed credits",
                idempotency_key=f"seed:{org.id}",
            )
            print(f"created org {org.id} with {credits_cents} credit cents")
        else:
            org = await session.get(Org, membership.org_id)
            assert org is not None
            print(f"org {org.id} already exists")

        existing = (
            await session.execute(select(Agent).where(Agent.org_id == org.id))
        ).scalars().all()
        if existing:
            print(f"org already has {len(existing)} agents; nothing else to do")
            _print_login(email, org.id)
            return

        # A deliberately cross-vendor team: routing work to the model that suits
        # it is the point of the product, and the seed should demonstrate it.
        pm = Agent(
            org_id=org.id,
            name="Nadia",
            role="product manager",
            model="claude-sonnet-5",
            effort="medium",
            system_prompt=(
                "Turn the request into a short, concrete spec: the goal, what is in "
                "and out of scope, and 3-6 acceptance criteria a reviewer could check. "
                "No implementation detail."
            ),
        )
        dev = Agent(
            org_id=org.id,
            name="Ravi",
            role="developer",
            model="claude-opus-5",
            effort="high",
            max_tokens=32_000,
            system_prompt=(
                "Implement the spec. Write complete, runnable code with no "
                "placeholders or TODOs, and emit each file as a labelled artifact "
                "block. Explain non-obvious decisions in two sentences at most."
            ),
        )
        reviewer = Agent(
            org_id=org.id,
            name="Mei",
            role="reviewer",
            model="gpt-5",
            effort="high",
            max_tokens=16_000,
            system_prompt=(
                "Review the implementation against the spec. Report every issue you "
                "find with a severity and your confidence, including ones you are "
                "unsure about — a later pass filters them. Then emit the corrected "
                "files as artifact blocks."
            ),
        )
        researcher = Agent(
            org_id=org.id,
            name="Tomas",
            role="researcher",
            model="gemini-2.5-flash",
            effort="medium",
            system_prompt=(
                "Gather and organise the background the team needs. Be concrete: "
                "name specific approaches, trade-offs and failure modes rather than "
                "listing general considerations."
            ),
        )
        skeptic = Agent(
            org_id=org.id,
            name="Iris",
            role="skeptic",
            model="grok-4",
            effort="high",
            system_prompt=(
                "Argue the strongest case against the proposal on the board. Attack "
                "only points you can actually justify; concede the rest explicitly."
            ),
        )
        session.add_all([pm, dev, reviewer, researcher, skeptic])
        await session.flush()

        session.add_all(
            [
                Workflow(
                    org_id=org.id,
                    name="Spec → Build → Review",
                    description="Linear delivery pipeline across three vendors.",
                    preset="pipeline",
                    graph={
                        "nodes": [
                            {"agent_id": pm.id},
                            {"agent_id": dev.id},
                            {"agent_id": reviewer.id},
                        ],
                        "max_steps": 6,
                        "max_cost_cents": 150,
                    },
                ),
                Workflow(
                    org_id=org.id,
                    name="Managed delivery",
                    description="A manager delegates to the team until the job is done.",
                    preset="supervisor",
                    graph={
                        "supervisor_agent_id": pm.id,
                        "workers": [researcher.id, dev.id, reviewer.id],
                        "max_rounds": 6,
                        "max_steps": 16,
                        "max_cost_cents": 300,
                    },
                ),
                Workflow(
                    org_id=org.id,
                    name="Decision debate",
                    description="Two models argue, a third rules.",
                    preset="debate",
                    graph={
                        "debaters": [researcher.id, skeptic.id],
                        "judge_agent_id": reviewer.id,
                        "rounds": 2,
                        "max_steps": 8,
                        "max_cost_cents": 200,
                    },
                ),
            ]
        )
        print(f"created 5 agents and 3 workflows in org {org.id}")
        _print_login(email, org.id)


def _print_login(email: str, org_id: str) -> None:
    print("\nSign in:")
    print(f"  POST /auth/login  {{'email': '{email}', 'password': '<your password>'}}")
    print(f"  Then use /orgs/{org_id}/... with the returned access token.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--org-name", default="Demo Workspace")
    parser.add_argument("--credits-cents", type=int, default=5000)
    args = parser.parse_args()

    async def _run() -> None:
        try:
            await seed(args.email, args.password, args.org_name, args.credits_cents)
        finally:
            await dispose_engine()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
