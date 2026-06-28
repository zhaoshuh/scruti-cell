# Batch Test 20 GSE Datasets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Scrut-Cell intelligent pipeline (inspect → plan → execute) on 20 GSE datasets per BUSINESS.md M0 validation strategy.

**Architecture:** 20 independent GSEs, each goes through 3-stage pipeline. 4 parallel agents handle 5 GSEs each.

**Tech Stack:** `scruticell` CLI, Python venv at `venv\Scripts\python.exe`

## Global Constraints

- Working directory: `E:\Scruti-Cell`
- Output directory: `E:\Scruti-Cell\batch\<GSE_ID>\`
- Python: `venv\Scripts\python.exe`
- CLI: `venv\Scripts\scruticell.exe`
- For each GSE, run: inspect → plan → execute (3 sequential steps)
- Do NOT modify any source files
- Each agent summarizes: which GSEs succeeded, which failed, key errors

## GSE List (20 total)

```
Group A (GSE312357, GSE327093, GSE333438, GSE297558, GSE290746)
Group B (GSE269042, GSE303943, GSE284668, GSE317309, GSE328098)
Group C (GSE294571, GSE284443, GSE297632, GSE312138, GSE295600)
Group D (GSE276139, GSE304864, GSE295518, GSE294490, GSE285606)
```

## Task Pattern (per GSE)

```bash
# Step 1: Inspect
venv\Scripts\scruticell.exe inspect GSE_XXX -o batch/GSE_XXX

# Step 2: Plan
venv\Scripts\scruticell.exe plan GSE_XXX --report batch/GSE_XXX/GSE_XXX_report.json -o batch/GSE_XXX

# Step 3: Execute
venv\Scripts\scruticell.exe execute GSE_XXX --plan-file batch/GSE_XXX/GSE_XXX_plan.json --report-file batch/GSE_XXX/GSE_XXX_report.json -o batch/GSE_XXX
```

---
### Task 1: Group A — 5 GSEs

**Files:** Output goes to `batch/GSE312357/`, `batch/GSE327093/`, `batch/GSE333438/`, `batch/GSE297558/`, `batch/GSE290746/`

- [ ] Run inspect for all 5
- [ ] Run plan for all 5  
- [ ] Run execute for all 5
- [ ] Report results

### Task 2: Group B — 5 GSEs

**Files:** Output goes to `batch/GSE269042/`, `batch/GSE303943/`, `batch/GSE284668/`, `batch/GSE317309/`, `batch/GSE328098/`

- [ ] Run inspect for all 5
- [ ] Run plan for all 5
- [ ] Run execute for all 5
- [ ] Report results

### Task 3: Group C — 5 GSEs

**Files:** Output goes to `batch/GSE294571/`, `batch/GSE284443/`, `batch/GSE297632/`, `batch/GSE312138/`, `batch/GSE295600/`

- [ ] Run inspect for all 5
- [ ] Run plan for all 5
- [ ] Run execute for all 5
- [ ] Report results

### Task 4: Group D — 5 GSEs

**Files:** Output goes to `batch/GSE276139/`, `batch/GSE304864/`, `batch/GSE295518/`, `batch/GSE294490/`, `batch/GSE285606/`

- [ ] Run inspect for all 5
- [ ] Run plan for all 5
- [ ] Run execute for all 5
- [ ] Report results
