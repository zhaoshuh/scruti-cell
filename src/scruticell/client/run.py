"""plan_run loop: create plan on the server, execute each step locally, feed back."""

from __future__ import annotations

import json
from pathlib import Path

from scruticell.client import api
from scruticell.client.executor import execute_step

_MAX_STEPS = 64


def plan_run(accession: str, source: str = "", workdir: str | None = None) -> dict:
    wd = Path(workdir or "output/client").resolve()
    wd.mkdir(parents=True, exist_ok=True)

    created = api.plan_create(accession, source)
    plan_id = created.get("plan_id")
    step = created.get("step")
    transcript: list[dict] = []
    guard = 0
    while step and guard < _MAX_STEPS:
        guard += 1
        res = execute_step(step, wd)
        out = api.plan_step(plan_id, res.get("ok", False), res.get("result", {}),
                            res.get("error", ""))
        transcript.append({
            "step": step.get("step"),
            "ok": bool(res.get("ok")),
            "error": res.get("error", ""),
            "next_done": bool(out.get("done")),
        })
        if out.get("done"):
            step = None
        else:
            step = out.get("step")
    return {
        "plan_id": plan_id,
        "accession": accession,
        "source": source,
        "done": not step,
        "steps_run": guard,
        "transcript": transcript,
        "workdir": str(wd),
    }


def print_plan_result(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))
