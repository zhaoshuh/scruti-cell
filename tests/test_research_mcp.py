"""Tests for the fast research tool (api.research + mcp.research via /api/ask)."""

from __future__ import annotations

from scruticell.client import api


def test_api_research_posts_ask(monkeypatch):
    import httpx

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"session_id": "s1", "reply": "请补充物种",
                    "status": "clarify", "done": False, "result": None}

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None,
                  follow_redirects=True):
        seen["url"] = url
        seen["json"] = json
        seen["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("SCRUTICELL_SERVER_URL", "http://srv")
    out = api.research("研究肺癌铁死亡", "")
    assert seen["url"] == "http://srv/api/ask"
    assert seen["json"] == {"question": "研究肺癌铁死亡", "session_id": ""}
    assert out["status"] == "clarify"
    # session_id must be echoed back for multi-turn
    api.research("人源", "s1")
    assert seen["json"]["session_id"] == "s1"


def test_research_mcp_roundtrip(monkeypatch):
    import asyncio

    import httpx

    from scruticell.client import mcp_server

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"session_id": "sx", "reply": "为您找到候选数据集",
                    "status": "ready", "done": False,
                    "result": {"recommendations": []}}

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None,
                  follow_redirects=True):
        calls.append((url, json))
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("SCRUTICELL_SERVER_URL", "http://srv")
    mcp = mcp_server._build_mcp()

    async def _go():
        return await mcp.call_tool("research",
                                   {"question": "研究肺癌", "session_id": ""})

    import json as _json

    r = _json.loads(asyncio.run(_go()).content[0].text)
    assert r["session_id"] == "sx"
    assert r["status"] == "ready"
    assert calls[0][0] == "http://srv/api/ask"
    assert calls[0][1]["question"] == "研究肺癌"
