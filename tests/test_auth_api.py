from __future__ import annotations

import pytest

from tests.conftest import TEST_PASSWORD


class TestRegistration:
    async def test_creates_a_user_org_and_owner_membership(self, client):
        response = await client.post(
            "/auth/register",
            json={
                "email": "new@example.com",
                "password": TEST_PASSWORD,
                "org_name": "Acme",
            },
        )
        assert response.status_code == 201, response.text
        token = response.json()["access_token"]

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        body = me.json()
        assert body["email"] == "new@example.com"
        assert body["orgs"][0]["role"] == "owner"
        assert body["orgs"][0]["name"] == "Acme"

    async def test_signup_bonus_is_credited(self, client):
        response = await client.post(
            "/auth/register",
            json={"email": "bonus@example.com", "password": TEST_PASSWORD},
        )
        token = response.json()["access_token"]
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["orgs"][0]["credits_cents"] == 100

    async def test_duplicate_email_is_rejected(self, client, account):
        response = await client.post(
            "/auth/register", json={"email": account.email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 409

    async def test_short_password_is_rejected(self, client):
        response = await client.post(
            "/auth/register", json={"email": "weak@example.com", "password": "short"}
        )
        assert response.status_code == 422

    async def test_email_is_normalised_to_lowercase(self, client):
        await client.post(
            "/auth/register",
            json={"email": "MixedCase@Example.com", "password": TEST_PASSWORD},
        )
        login = await client.post(
            "/auth/login",
            json={"email": "mixedcase@example.com", "password": TEST_PASSWORD},
        )
        assert login.status_code == 200


class TestLogin:
    async def test_valid_credentials_return_tokens(self, client, account):
        response = await client.post(
            "/auth/login", json={"email": account.email, "password": account.password}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_wrong_password_is_rejected(self, client, account):
        response = await client.post(
            "/auth/login", json={"email": account.email, "password": "wrong-password-here"}
        )
        assert response.status_code == 401

    async def test_unknown_and_wrong_password_are_indistinguishable(self, client, account):
        """The two responses must match, or the endpoint becomes an account
        enumeration oracle."""
        unknown = await client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": TEST_PASSWORD},
        )
        wrong = await client.post(
            "/auth/login", json={"email": account.email, "password": "definitely-wrong"}
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]


class TestTokens:
    async def test_protected_route_requires_a_token(self, client):
        assert (await client.get("/auth/me")).status_code == 401

    async def test_garbage_token_is_rejected(self, client):
        response = await client.get(
            "/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code == 401

    async def test_refresh_rotates_the_token(self, client, account):
        first = await client.post(
            "/auth/login", json={"email": account.email, "password": account.password}
        )
        original = first.json()["refresh_token"]

        rotated = await client.post("/auth/refresh", json={"refresh_token": original})
        assert rotated.status_code == 200
        assert rotated.json()["refresh_token"] != original

    async def test_replaying_a_spent_token_revokes_the_family(self, client, account):
        login = await client.post(
            "/auth/login", json={"email": account.email, "password": account.password}
        )
        original = login.json()["refresh_token"]

        rotated = await client.post("/auth/refresh", json={"refresh_token": original})
        newest = rotated.json()["refresh_token"]

        # Replaying the spent token is the signature of a stolen credential.
        replay = await client.post("/auth/refresh", json={"refresh_token": original})
        assert replay.status_code == 401
        assert "reuse" in replay.json()["detail"]

        # The whole family dies with it, including the token issued from it.
        after = await client.post("/auth/refresh", json={"refresh_token": newest})
        assert after.status_code == 401

    async def test_changing_the_password_invalidates_existing_access_tokens(
        self, client, account
    ):
        response = await client.post(
            "/auth/change-password",
            json={"current_password": account.password, "new_password": "a-brand-new-passphrase"},
            headers=account.headers,
        )
        assert response.status_code == 204

        # The old access token carries a stale epoch and must stop working.
        stale = await client.get("/auth/me", headers=account.headers)
        assert stale.status_code == 401

    async def test_change_password_requires_the_current_one(self, client, account):
        response = await client.post(
            "/auth/change-password",
            json={"current_password": "not-the-password", "new_password": "another-passphrase"},
            headers=account.headers,
        )
        assert response.status_code == 403

    async def test_logout_everywhere_invalidates_access_tokens(self, client, account):
        assert (
            await client.post("/auth/logout-all", headers=account.headers)
        ).status_code == 204
        assert (await client.get("/auth/me", headers=account.headers)).status_code == 401


class TestTenantIsolation:
    """Cross-tenant access is the failure mode that matters most in a
    multi-organisation product."""

    @pytest.fixture
    async def other(self, client):
        response = await client.post(
            "/auth/register",
            json={"email": "other@example.com", "password": TEST_PASSWORD},
        )
        token = response.json()["access_token"]
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        return {
            "token": token,
            "org_id": me.json()["orgs"][0]["id"],
            "headers": {"Authorization": f"Bearer {token}"},
        }

    async def test_another_org_is_not_readable(self, client, account, other):
        response = await client.get(
            f"/orgs/{other['org_id']}/agents", headers=account.headers
        )
        # 404 rather than 403: a 403 would confirm the organisation exists.
        assert response.status_code == 404

    async def test_agents_are_scoped_to_their_org(self, client, account, other):
        created = await client.post(
            f"/orgs/{account.org_id}/agents",
            json={"name": "Mine", "role": "dev", "model": "claude-sonnet-5"},
            headers=account.headers,
        )
        agent_id = created.json()["id"]

        leaked = await client.get(
            f"/orgs/{other['org_id']}/agents/{agent_id}", headers=other["headers"]
        )
        assert leaked.status_code == 404
