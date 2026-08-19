import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from observability.config import config
from observability.context import get_trace_id
from observability.metrics import record_evaluation_metric

@dataclass
class StructuredEvaluationResult:
    overall_score: int          # 0 - 100
    relevance_score: int        # 0 - 100
    groundedness_score: int     # 0 - 100
    completeness_score: int     # 0 - 100
    safety_score: int           # 0 - 100
    citation_quality: int       # 0 - 100
    evaluator_type: str         # "heuristic" | "llm_as_judge"
    evaluation_latency_ms: float
    evaluation_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _count_markdown_links(text: str) -> int:
    return len(re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", text or ""))


def evaluate_response_heuristically(
    query: str,
    final_response: str,
    route: str,
    web_notes: str = "",
    rag_notes: str = "",
    kg_notes: str = "",
    math_result: str = "",
    safety_assessment: str = "SAFE"
) -> StructuredEvaluationResult:
    """
    Deterministic heuristic evaluation path (0-100 scale).
    Fast, reliable, does not require an LLM call.
    """
    t0 = time.perf_counter()
    reasons: List[str] = []

    # 1. Safety Score
    if safety_assessment.upper() == "UNSAFE":
        safety_score = 0
        reasons.append("Safety check failed: unsafe content flagged.")
    else:
        safety_score = 100
        reasons.append("Safety check passed.")

    # 2. Relevance Score
    resp_len = len(final_response.strip())
    if resp_len == 0:
        relevance_score = 0
        reasons.append("Response is empty.")
    elif resp_len < 15:
        relevance_score = 30
        reasons.append("Response is extremely short.")
    else:
        relevance_score = 90
        # Check query word overlap
        query_words = set(re.findall(r"\w{4,}", query.lower()))
        resp_lower = final_response.lower()
        if query_words:
            matched = sum(1 for w in query_words if w in resp_lower)
            overlap_ratio = matched / len(query_words)
            if overlap_ratio >= 0.5:
                relevance_score = 95
                reasons.append("Strong query term overlap in response.")
            else:
                reasons.append("Moderate query term coverage.")

    # 3. Citation Quality & Groundedness
    citation_quality = 80
    groundedness_score = 80
    clean_route = (route or "general").lower()

    if clean_route in {"web", "hybrid"}:
        links = _count_markdown_links(final_response)
        if links >= 2:
            citation_quality = 95
            groundedness_score = 90
            reasons.append(f"Web answer contains {links} markdown source citations.")
        elif links == 1:
            citation_quality = 70
            groundedness_score = 75
            reasons.append("Web answer contains 1 source citation.")
        else:
            citation_quality = 20
            groundedness_score = 50
            reasons.append("Web answer is missing markdown citations.")

        if web_notes and not web_notes.startswith("Web retrieval failed:"):
            groundedness_score = min(100, groundedness_score + 10)
        else:
            groundedness_score = max(20, groundedness_score - 30)
            reasons.append("Web retrieval failed or returned empty evidence.")

    elif clean_route in {"rag", "hybrid"}:
        if rag_notes and not rag_notes.startswith("Local RAG retrieval failed:") and "not required" not in rag_notes.lower():
            groundedness_score = 90
            citation_quality = 85
            reasons.append("Local RAG retrieval evidence used.")
        else:
            groundedness_score = 40
            reasons.append("Local RAG evidence missing or failed.")

    elif clean_route == "kg":
        if kg_notes and "failed" not in kg_notes.lower() and "not required" not in kg_notes.lower():
            groundedness_score = 90
            citation_quality = 85
            reasons.append("Knowledge Graph relationship path used.")
        else:
            groundedness_score = 40

    elif clean_route == "math":
        if math_result and "failed" not in math_result.lower():
            groundedness_score = 100
            citation_quality = 90
            reasons.append("Math evaluation completed successfully.")
        else:
            groundedness_score = 30
            reasons.append("Math evaluation failed.")

    # 4. Completeness Score
    if resp_len > 1800:
        completeness_score = 80
        reasons.append("Response is very detailed, slightly verbose.")
    elif resp_len >= 100:
        completeness_score = 90
        reasons.append("Response length is optimal.")
    elif resp_len >= 30:
        completeness_score = 75
    else:
        completeness_score = 40

    # 5. Overall Score (Weighted combination)
    if safety_score == 0:
        overall_score = 0
    else:
        overall_score = int(
            (0.30 * relevance_score) +
            (0.30 * groundedness_score) +
            (0.20 * completeness_score) +
            (0.10 * citation_quality) +
            (0.10 * safety_score)
        )

    overall_score = max(0, min(100, overall_score))
    t1 = time.perf_counter()
    latency_ms = (t1 - t0) * 1000.0

    eval_reason = "; ".join(reasons)

    record_evaluation_metric(
        evaluator_type="heuristic",
        overall=float(overall_score),
        relevance=float(relevance_score),
        groundedness=float(groundedness_score),
        completeness=float(completeness_score),
        safety=float(safety_score)
    )

    return StructuredEvaluationResult(
        overall_score=overall_score,
        relevance_score=relevance_score,
        groundedness_score=groundedness_score,
        completeness_score=completeness_score,
        safety_score=safety_score,
        citation_quality=citation_quality,
        evaluator_type="heuristic",
        evaluation_latency_ms=latency_ms,
        evaluation_reason=eval_reason
    )
