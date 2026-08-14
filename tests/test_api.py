from __future__ import annotations

import pytest

from tests.conftest import TEST_PASSWORD


@pytest.fixture
async def agent_ids(client, account) -> dict[str, str]:
    ids = {}
    for name, role, model in [
        ("Nadia", "pm", "claude-sonnet-5"),
        ("Ravi", "dev", "claude-opus-5"),
        ("Mei", "reviewer", "gpt-5"),
    ]:
        response = await client.post(
            f"/orgs/{account.org_id}/agents",
            json={"name": name, "role": role, "model": model},
            headers=account.headers,
        )
        assert response.status_code == 201, response.text
        ids[role] = response.json()["id"]
    return ids


@pytest.fixture
async def workflow_id(client, account, agent_ids) -> str:
    response = await client.post(
        f"/orgs/{account.org_id}/workflows",
        json={
            "name": "Delivery",
            "preset": "pipeline",
            "graph": {
                "nodes": [
                    {"agent_id": agent_ids["pm"]},
                    {"agent_id": agent_ids["dev"]},
                    {"agent_id": agent_ids["reviewer"]},
                ],
                "max_cost_cents": 50,
            },
        },
        headers=account.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestAgents:
    async def test_crud(self, client, account):
        created = await client.post(
            f"/orgs/{account.org_id}/agents",
            json={"name": "Ann", "role": "dev", "model": "claude-sonnet-5"},
            headers=account.headers,
        )
        assert created.status_code == 201
        agent_id = created.json()["id"]

        patched = await client.patch(
            f"/orgs/{account.org_id}/agents/{agent_id}",
            json={"effort": "high"},
            headers=account.headers,
        )
        assert patched.json()["effort"] == "high"
        assert patched.json()["name"] == "Ann", "a partial update must not clear fields"

        deleted = await client.delete(
            f"/orgs/{account.org_id}/agents/{agent_id}", headers=account.headers
        )
        assert deleted.status_code == 204

        listed = await client.get(
            f"/orgs/{account.org_id}/agents", headers=account.headers
        )
        assert listed.json() == [], "a deactivated agent must be hidden by default"

    async def test_unknown_model_is_rejected(self, client, account):
        response = await client.post(
            f"/orgs/{account.org_id}/agents",
            json={"name": "Ann", "role": "dev", "model": "gpt-42-ultra"},
            headers=account.headers,
        )
        assert response.status_code == 422
        assert "registry" in response.json()["detail"]

    @pytest.mark.parametrize(
        "model", ["claude-opus-5", "gpt-5", "grok-4", "gemini-2.5-pro"]
    )
    async def test_every_vendor_is_accepted(self, client, account, model):
        response = await client.post(
            f"/orgs/{account.org_id}/agents",
            json={"name": f"A-{model}", "role": "dev", "model": model},
            headers=account.headers,
        )
        assert response.status_code == 201, response.text


class TestWorkflows:
    async def test_graph_is_validated_on_save(self, client, account, agent_ids):
        response = await client.post(
            f"/orgs/{account.org_id}/workflows",
            json={"name": "Broken", "preset": "pipeline", "graph": {}},
            headers=account.headers,
        )
        assert response.status_code == 422
        assert "nodes" in response.json()["detail"]

    async def test_unknown_agent_reference_is_rejected(self, client, account):
        response = await client.post(
            f"/orgs/{account.org_id}/workflows",
            json={
                "name": "Ghost",
                "preset": "pipeline",
                "graph": {"nodes": [{"agent_id": "does-not-exist"}]},
            },
            headers=account.headers,
        )
        assert response.status_code == 422
        assert "unknown or inactive agents" in response.json()["detail"]

    async def test_presets_are_documented(self, client, account):
        response = await client.get("/presets", headers=account.headers)
        names = {p["name"] for p in response.json()}
        assert names == {
            "pipeline",
            "supervisor",
            "debate",
            "blackboard",
            "swarm",
            "custom",
        }


class TestRuns:
    async def test_creating_a_run_enqueues_a_job(self, client, account, workflow_id, app):
        response = await client.post(
            f"/orgs/{account.org_id}/runs",
            json={"workflow_id": workflow_id, "input": "build a URL shortener"},
            headers=account.headers,
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        assert response.json()["status"] == "queued"

        jobs = app.state.test_queue.jobs
        assert jobs and jobs[0][0] == "run_workflow"
        assert jobs[0][1][0] == run_id

    async def test_idempotency_key_returns_the_same_run(
        self, client, account, workflow_id, app
    ):
        payload = {"workflow_id": workflow_id, "input": "x", "idempotency_key": "abc-123"}
        first = await client.post(
            f"/orgs/{account.org_id}/runs", json=payload, headers=account.headers
        )
        second = await client.post(
            f"/orgs/{account.org_id}/runs", json=payload, headers=account.headers
        )
        assert first.json()["id"] == second.json()["id"]
        assert len(app.state.test_queue.jobs) == 1, "a duplicate must not enqueue twice"

    async def test_run_is_refused_without_credit(self, client, account, workflow_id, session):
        from core.models import Org

        org = await session.get(Org, account.org_id)
        org.credits_cents = 0
        await session.commit()

        response = await client.post(
            f"/orgs/{account.org_id}/runs",
            json={"workflow_id": workflow_id, "input": "x"},
            headers=account.headers,
        )
        assert response.status_code == 402
        assert "insufficient credits" in response.json()["detail"]

    async def test_byok_run_does_not_need_credit(
        self, client, account, workflow_id, session
    ):
        from core.models import Org

        org = await session.get(Org, account.org_id)
        org.credits_cents = 0
        await session.commit()

        response = await client.post(
            f"/orgs/{account.org_id}/runs",
            json={"workflow_id": workflow_id, "input": "x", "key_mode": "byok"},
            headers=account.headers,
        )
        assert response.status_code == 201

    async def test_cancelling_a_queued_run_stops_it(self, client, account, workflow_id):
        created = await client.post(
            f"/orgs/{account.org_id}/runs",
            json={"workflow_id": workflow_id, "input": "x"},
            headers=account.headers,
        )
        run_id = created.json()["id"]

        cancelled = await client.post(
            f"/orgs/{account.org_id}/runs/{run_id}/cancel", headers=account.headers
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

    async def test_cancel_sets_the_flag_the_engine_reads(
        self, client, account, workflow_id, redis_client
    ):
        from core import events as ev

        created = await client.post(
            f"/orgs/{account.org_id}/runs",
            json={"workflow_id": workflow_id, "input": "x"},
            headers=account.headers,
        )
        run_id = created.json()["id"]
        await client.post(
            f"/orgs/{account.org_id}/runs/{run_id}/cancel", headers=account.headers
        )
        assert await ev.is_cancelled(run_id, redis_client) is True

    async def test_run_from_another_org_is_not_visible(self, client, account, workflow_id):
        created = await client.post(
            f"/orgs/{account.org_id}/runs",
            json={"workflow_id": workflow_id, "input": "x"},
            headers=account.headers,
        )
        run_id = created.json()["id"]

        register = await client.post(
            "/auth/register",
            json={"email": "intruder@example.com", "password": TEST_PASSWORD},
        )
        token = register.json()["access_token"]
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        other_org = me.json()["orgs"][0]["id"]

        response = await client.get(
            f"/orgs/{other_org}/runs/{run_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


class TestProviderKeys:
    async def test_plaintext_is_never_returned(self, client, account):
        secret = "sk-ant-api03-super-secret-value-1234"
        created = await client.post(
            f"/orgs/{account.org_id}/keys",
            json={"provider": "anthropic", "api_key": secret},
            headers=account.headers,
        )
        assert created.status_code == 201
        body = created.text
        assert secret not in body
        assert created.json()["mask"].endswith("1234")

        listed = await client.get(f"/orgs/{account.org_id}/keys", headers=account.headers)
        assert secret not in listed.text

    async def test_storing_a_key_twice_replaces_it(self, client, account):
        for value in ("sk-ant-first-value-000000", "sk-ant-second-value-11111"):
            await client.post(
                f"/orgs/{account.org_id}/keys",
                json={"provider": "anthropic", "api_key": value},
                headers=account.headers,
            )
        listed = await client.get(f"/orgs/{account.org_id}/keys", headers=account.headers)
        assert len(listed.json()) == 1
        assert listed.json()[0]["mask"].endswith("1111")

    async def test_the_stored_ciphertext_decrypts_back(self, client, account, session):
        from sqlalchemy import select

        from core.crypto import decrypt_secret
        from core.models import ApiKey

        secret = "sk-ant-api03-roundtrip-check-99999"
        await client.post(
            f"/orgs/{account.org_id}/keys",
            json={"provider": "anthropic", "api_key": secret},
            headers=account.headers,
        )
        row = (
            await session.execute(select(ApiKey).where(ApiKey.org_id == account.org_id))
        ).scalar_one()
        assert row.ciphertext != secret
        assert decrypt_secret(row.ciphertext, aad=account.org_id) == secret


class TestModelCatalogue:
    async def test_lists_models_from_every_provider(self, client, account):
        response = await client.get(
            f"/orgs/{account.org_id}/models", headers=account.headers
        )
        assert response.status_code == 200
        providers = {m["provider"] for m in response.json()}
        assert {"anthropic", "openai", "xai", "google"} <= providers

    async def test_availability_reflects_configured_credentials(self, client, account):
        response = await client.get(
            f"/orgs/{account.org_id}/models", headers=account.headers
        )
        # The test environment configures a platform key for all four vendors.
        assert all(m["available"] for m in response.json())


class TestBillingEndpoints:
    async def test_balance_and_ledger(self, client, account):
        balance = await client.get(
            f"/orgs/{account.org_id}/billing/balance", headers=account.headers
        )
        assert balance.json()["credits_cents"] == 100

        await client.post(
            f"/orgs/{account.org_id}/billing/topup",
            json={"amount_cents": 900, "description": "manual"},
            headers=account.headers,
        )
        after = await client.get(
            f"/orgs/{account.org_id}/billing/balance", headers=account.headers
        )
        assert after.json()["credits_cents"] == 1000

        ledger = await client.get(
            f"/orgs/{account.org_id}/billing/ledger", headers=account.headers
        )
        assert len(ledger.json()) == 2

    async def test_topup_requires_the_owner_role(self, client, account, session):
        from sqlalchemy import select

        from core.models import Membership

        membership = (
            await session.execute(
                select(Membership).where(Membership.user_id == account.user_id)
            )
        ).scalar_one()
        membership.role = "member"
        await session.commit()

        response = await client.post(
            f"/orgs/{account.org_id}/billing/topup",
            json={"amount_cents": 100},
            headers=account.headers,
        )
        assert response.status_code == 403


class TestMembers:
    async def test_an_org_must_keep_an_owner(self, client, account):
        response = await client.patch(
            f"/orgs/{account.org_id}/members/{account.user_id}",
            json={"role": "member"},
            headers=account.headers,
        )
        assert response.status_code == 409
        assert "at least one owner" in response.json()["detail"]

    async def test_adding_an_unknown_email_is_refused(self, client, account):
        response = await client.post(
            f"/orgs/{account.org_id}/members",
            json={"email": "ghost@example.com", "role": "member"},
            headers=account.headers,
        )
        assert response.status_code == 404


class TestOperationalEndpoints:
    async def test_health_needs_no_dependencies(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_metrics_are_exposed(self, client):
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "python_info" in response.text

    async def test_responses_carry_a_request_id(self, client):
        response = await client.get("/health")
        assert response.headers.get("x-request-id")

    async def test_security_headers_are_set(self, client):
        response = await client.get("/health")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
