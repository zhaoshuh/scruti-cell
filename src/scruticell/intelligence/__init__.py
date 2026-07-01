"""Intelligence layer for LLM-powered GEO data understanding.

Provides M1 (Data Detective) and M2 (Strategy Engine):

M1 - Data Detective:
- Decompressor: recursive archive extraction with path tracking
- Inspector: structured content summaries for all file types
- Reporter: packaging extracted info as LLM-ready JSON

M3 - Execution Engine:
- PlanExecutor: execute processing plan using pipeline nodes
- ExecutionMonitor: error reflection + plan revision
- ExecutionResult / ExecutionStep / ProvenanceRecorder: traceability
"""

from scruticell.intelligence.decompressor import Decompressor, DecompressionNode
from scruticell.intelligence.inspector import Inspector, FileSummary
from scruticell.intelligence.reporter import Reporter, GseReport, GseInfo, GsmInfo
from scruticell.intelligence.matcher import StrategyMatcher, MatchedStrategy, GseFingerprint
from scruticell.intelligence.planner import ProcessingPlan, MatrixMapping, BatchMapping, FieldMapping
from scruticell.intelligence.generator import PlanGenerator, PlanValidator, PlanOrchestrator
from scruticell.intelligence.executor import (
    PlanExecutor,
    ExecutionMonitor,
    ExecutionResult,
    ExecutionStep,
    ProvenanceRecorder,
)
from scruticell.intelligence.extractors import MultiStepExtractor
from scruticell.intelligence.verifier import BatchVerifier, VerificationReport, BatchMismatch

__all__ = [
    # M1
    "Decompressor", "DecompressionNode",
    "Inspector", "FileSummary",
    "Reporter", "GseReport", "GseInfo", "GsmInfo",
    # M2
    "StrategyMatcher", "MatchedStrategy", "GseFingerprint",
    "ProcessingPlan", "MatrixMapping", "BatchMapping", "FieldMapping",
    "PlanGenerator", "PlanValidator", "PlanOrchestrator",
    "MultiStepExtractor",
    # M3
    "PlanExecutor", "ExecutionMonitor",
    "ExecutionResult", "ExecutionStep", "ProvenanceRecorder",
    # Verification
    "BatchVerifier", "VerificationReport", "BatchMismatch",
]
