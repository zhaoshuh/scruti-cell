"""HTTP client for the remote omics-discovery server (coding-agent mode).

The customer machine (where the coding agent runs) uses the user's API key to
call the server for search/report/plan; heavy work stays local.
"""

from __future__ import annotations

import os


def server_url() -> str:
    return os.environ.get("SCRUTICELL_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")


def api_key() -> str:
    return os.environ.get("SCRUTICELL_API_KEY", "")


def _headers() -> dict:
    key = api_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _post(path: str, payload: dict, timeout: float = 180) -> dict:
    import httpx

    resp = httpx.post(f"{server_url()}{path}", json=payload, headers=_headers(),
                      timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: dict | None = None, timeout: float = 180) -> dict:
    import httpx

    resp = httpx.get(f"{server_url()}{path}", params=params, headers=_headers(),
                     timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def search_datasets(question: str = "", task: str = "", top_n: int = 10) -> dict:
    return _post("/api/search", {"question": question, "task": task,
                                 "top_n": int(top_n or 10)})


def dataset_report(accession: str, deep: bool = False) -> dict:
    return _get(f"/api/report/{accession}", {"deep": 1 if deep else 0})


def plan_create(accession: str, source: str = "") -> dict:
    return _post("/api/plan/create", {"accession": accession, "source": source})


def plan_step(plan_id: str, ok: bool, result: dict | None = None,
              error: str = "") -> dict:
    return _post("/api/plan/step", {"plan_id": plan_id, "ok": bool(ok),
                                    "result": result or {}, "error": error})


def plan_status(plan_id: str) -> dict:
    return _get(f"/api/plan/status/{plan_id}")
