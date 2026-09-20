"""Tests for the conversational research tool (api.ask_session + mcp.research)."""

from __future__ import annotations

from scruticell.client import api

SSE_BODY = (
    'data: {"type": "status", "text": "正在检索…"}\n\n'
    'data: {"type": "token", "id": "final", "text": "为您推荐 2 个数据集。"}\n\n'
    'data: {"type": "result", "result": {"recommendations": [{"candidate": '
    '{"accession": "GSE135624", "source": "geo_ncbi"}}]}}\n\n'
    'data: {"type": "done"}\n\n'
)


def test_parse_sse_aggregates():
    out = api._parse_sse(SSE_BODY)
    assert out["reply"] == "为您推荐 2 个数据集。"
    assert out["done"] is True
    assert out["result"]["recommendations"][0]["candidate"]["accession"] == \
        "GSE135624"


def test_ask_session_posts_and_parses(monkeypatch):
    import httpx

    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def text(self):
            return SSE_BODY

    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None,
                  follow_redirects=True):
        seen["url"] = url
        seen["json"] = json
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("SCRUTICELL_SERVER_URL", "http://srv")
    out = api.ask_session("s1", "研究肺癌铁死亡")
    assert seen["url"] == "http://srv/api/sessions/s1/message"
    assert seen["json"] == {"message": "研究肺癌铁死亡"}
    assert out["done"] is True


def test_research_creates_session_then_continues(monkeypatch):
    import asyncio

    import httpx

    from scruticell.client import mcp_server

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"session_id": "sx"}

        @property
        def text(self):
            return SSE_BODY

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None,
                  follow_redirects=True):
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("SCRUTICELL_SERVER_URL", "http://srv")
    mcp = mcp_server._build_mcp()

    async def _go():
        return await mcp.call_tool("research",
                                   {"question": "研究肺癌", "session_id": ""})

    def _json(r):
        import json as _json

        return _json.loads(r.content[0].text)

    first = _json(asyncio.run(_go()))
    assert first["session_id"] == "sx"
    assert calls[0] == "http://srv/api/sessions"
    asyncio.run(mcp.call_tool(
        "research", {"question": "用人源 RNA-seq",
                     "session_id": first["session_id"]}))
    assert calls[1] == f"http://srv/api/sessions/{first['session_id']}/message"
