"""tests/test_free_tiers_routes.py — M4 free-tier catalog HTTP surface.

Covers moa_gateway/routes/free_tiers.py over the REAL bundled 456-entry
catalog (no fixtures, no mocks): auth gating, filter/pagination semantics,
validation errors, per-key lookup, and the enabled=503 opt-in gate.

The HTTP app is self-built per the frozen architecture contract:
    app = FastAPI(); app.include_router(routes.free_tiers.router)
so these tests never depend on moa_gateway/server.py or routes/__init__.py.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.free_tiers.catalog import FreeTierCatalog

API_KEY = "free-tiers-test-key-001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def app(gateway_settings):
    from moa_gateway.routes.free_tiers import router

    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
def gateway_settings(monkeypatch):
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        }
    )
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    return settings


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://freetiers.test") as ac:
        yield ac


async def test_http_requires_api_key(client):
    assert (await client.get("/v1/free-tiers")).status_code == 401
    assert (await client.get("/v1/free-tiers/whatever")).status_code == 401


async def test_list_returns_real_bundled_catalog(client):
    response = await client.get("/v1/free-tiers", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 50
    # Bundled curated baseline is the 456-entry OmniRoute catalog.
    assert payload["total"] >= 400
    assert len(payload["items"]) == 50
    first = payload["items"][0]
    for field in ("provider", "modelId", "displayName", "monthlyTokens", "freeType", "tos"):
        assert field in first


async def test_list_provider_filter_is_self_consistent(client):
    page = (await client.get("/v1/free-tiers", headers=AUTH)).json()
    provider = page["items"][0]["provider"]
    filtered = (
        await client.get("/v1/free-tiers", params={"provider": provider}, headers=AUTH)
    ).json()
    assert filtered["total"] >= 1
    assert all(item["provider"] == provider for item in filtered["items"])


async def test_list_pagination_is_stable_and_sorted(client):
    first_page = (
        await client.get("/v1/free-tiers", params={"page_size": 10}, headers=AUTH)
    ).json()
    second_page = (
        await client.get(
            "/v1/free-tiers", params={"page": 2, "page_size": 10}, headers=AUTH
        )
    ).json()
    first_keys = [FreeTierCatalog.entry_key(e) for e in first_page["items"]]
    second_keys = [FreeTierCatalog.entry_key(e) for e in second_page["items"]]
    assert not set(first_keys) & set(second_keys)
    tokens = [e["monthlyTokens"] for e in first_page["items"]]
    assert tokens == sorted(tokens, reverse=True)


async def test_list_rejects_unknown_regime(client):
    response = await client.get(
        "/v1/free-tiers", params={"regime": "not-a-regime"}, headers=AUTH
    )
    assert response.status_code == 422


async def test_list_rejects_out_of_range_page_size(client):
    response = await client.get(
        "/v1/free-tiers", params={"page_size": 201}, headers=AUTH
    )
    assert response.status_code == 422


async def test_get_entry_by_key_roundtrip(client):
    page = (await client.get("/v1/free-tiers", params={"page_size": 5}, headers=AUTH)).json()
    entry = page["items"][0]
    key = FreeTierCatalog.entry_key(entry)
    response = await client.get(f"/v1/free-tiers/{key}", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == entry


async def test_get_unknown_key_returns_404(client):
    response = await client.get("/v1/free-tiers/no-such-key-xyz", headers=AUTH)
    assert response.status_code == 404


async def test_disabled_capability_returns_503(app, gateway_settings, monkeypatch):
    gateway_settings.free_tiers.enabled = False
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://freetiers.test") as ac:
        assert (await ac.get("/v1/free-tiers", headers=AUTH)).status_code == 503
        assert (
            await ac.get("/v1/free-tiers/some-key", headers=AUTH)
        ).status_code == 503
