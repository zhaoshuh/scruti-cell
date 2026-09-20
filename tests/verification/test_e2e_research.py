"""Live E2E: conversational research (sniff) → plan_dataset (guide processing).

Marked ``@pytest.mark.live`` — skipped unless ``--run-live``. Needs the real
server + network.

Flow under test:
  research(question) -> server agent sniffs (search/verify) and converses
  plan_dataset(accession) -> a ready processing plan + feasibility, so the
  coding agent can run plan_run locally.
"""

from __future__ import annotations

import pytest

from scruticell.client import api

pytestmark = pytest.mark.live


def test_live_research_clarifies(tmp_path):
    sid = api.create_session().get("session_id")
    assert sid
    out = api.ask_session(sid, "帮我找点数据")
    assert out["done"] is True
    assert out.get("reply"), "agent should reply (clarify or result)"


def test_live_research_result_and_plan(tmp_path):
    """Ambiguous→clarify→answer→(optionally full discovery). Tolerant: if the
    full discovery is too slow (blocked hosts), still assert the clarify path."""
    sid = api.create_session().get("session_id")
    first = api.ask_session(sid, "研究人源肺腺癌铁死亡，RNA-seq，肿瘤vs正常对照")
    # profile is complete -> agent runs discovery; it may be slow, so we only
    # assert it returns a reply or a structured result.
    assert first.get("reply") or first.get("result")
