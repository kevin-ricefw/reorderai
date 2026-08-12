from __future__ import annotations

import json
from types import SimpleNamespace

from api.services import product_enrichment_service as service


def _fake_openai_client(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    completion = SimpleNamespace(choices=[choice])
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: completion)
        )
    )


def test_enrich_from_name_parses_json(monkeypatch):
    payload = {"product_name": "Chips", "brand": "Lay's", "size": "8 oz"}
    monkeypatch.setattr(
        service, "_get_client", lambda: _fake_openai_client(json.dumps(payload))
    )
    assert service.enrich_from_name("LAYS CLASSIC CHIPS 8OZ") == payload


def test_enrich_from_name_defaults_missing_fields_to_na(monkeypatch):
    monkeypatch.setattr(service, "_get_client", lambda: _fake_openai_client("{}"))
    assert service.enrich_from_name("BANANA") == {
        "product_name": "n/a",
        "brand": "n/a",
        "size": "n/a",
    }


def test_enrich_missing_updates_each_row_and_reports_failures(monkeypatch):
    rows = [{"id": 1, "name": "BANANA"}, {"id": 2, "name": "BAD ITEM"}]
    updates: list[tuple] = []

    class FakeRepo:
        def __init__(self, tenant_id=None):
            pass

        def find_missing(self, limit=None):
            return rows

        def update_fields(self, product_id, product_name, brand, size):
            updates.append((product_id, product_name, brand, size))

    def fake_enrich_from_name(name: str) -> dict:
        if name == "BAD ITEM":
            raise RuntimeError("llm timeout")
        return {"product_name": "n/a", "brand": "n/a", "size": "n/a"}

    monkeypatch.setattr(service, "ProductRepository", FakeRepo)
    monkeypatch.setattr(service, "enrich_from_name", fake_enrich_from_name)

    result = service.enrich_missing()

    assert result["updated"] == 1
    assert updates == [(1, "n/a", "n/a", "n/a")]
    assert result["failed"] == [{"id": 2, "error": "llm timeout"}]
