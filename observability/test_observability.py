import pytest
import os
import gc
import sqlite3
import tempfile
from pathlib import Path

from observability.context import set_trace_id, get_trace_id, set_user_id, get_user_id, set_thread_id, get_thread_id
from observability.redaction import scrub_secrets, redact_content
from observability.evaluator import evaluate_response_heuristically
from observability.tracer import ObservabilityTracer, trace_agent, set_persistence_store
from service.persistence_store import SQLiteConversationStore


def test_context_vars():
    t_id = set_trace_id("test-trace-123")
    assert get_trace_id() == "test-trace-123"

    u_id = set_user_id("user-456")
    assert get_user_id() == "user-456"

    th_id = set_thread_id("thread-789")
    assert get_thread_id() == "thread-789"


def test_redaction():
    text_with_key = "My key is sk-1234567890abcdef1234567890abcdef1234 and pass=SecretPass123!"
    scrubbed = scrub_secrets(text_with_key)
    assert "sk-1234567890abcdef1234567890abcdef1234" not in scrubbed
    assert "REDACTED" in scrubbed

    metadata = {"api_key": "sk-12345", "password": "supersecretpassword", "normal": "hello"}
    redacted_meta = redact_content(metadata)
    assert "REDACTED" in redacted_meta["api_key"]
    assert "REDACTED" in redacted_meta["password"]
    assert redacted_meta["normal"] == "hello"


def test_evaluator():
    res = evaluate_response_heuristically(
        query="What is FastAPI?",
        final_response="FastAPI is a modern, fast (high-performance) web framework for building APIs with Python.",
        route="general",
        safety_assessment="SAFE"
    )
    assert res.overall_score >= 50
    assert res.safety_score == 100
    assert res.relevance_score >= 50

    # Test unsafe evaluation
    res_unsafe = evaluate_response_heuristically(
        query="bad input",
        final_response="unsafe content",
        route="general",
        safety_assessment="UNSAFE"
    )
    assert res_unsafe.overall_score == 0
    assert res_unsafe.safety_score == 0


@pytest.mark.asyncio
async def test_sqlite_observability_store():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = SQLiteConversationStore(db_path, namespace="test_ns")
        store.setup()

        # Save trace
        store.save_trace("trace-001", user_id="u1", thread_id="t1", status="success", duration_ms=150.0, metadata={"test": True})

        # Save agent execution
        store.save_agent_execution(
            trace_id="trace-001",
            agent_name="test_agent",
            user_id="u1",
            thread_id="t1",
            start_time="2026-08-19T12:00:00Z",
            end_time="2026-08-19T12:00:00.150Z",
            duration_ms=150.0,
            success=True,
            route_selected="general"
        )

        # Save LLM call
        store.save_llm_call(
            trace_id="trace-001",
            agent_name="test_agent",
            model_name="gpt-4o-mini",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            latency_ms=120.0,
            estimated_cost=0.0001,
            success=True
        )

        # Save retrieval event
        store.save_retrieval_event(
            trace_id="trace-001",
            query="test query",
            num_retrieved_docs=3,
            retrieval_latency_ms=15.0,
            reranking_latency_ms=5.0,
            top_k=3,
            retrieval_method="vector",
            vector_count=3,
            bm25_count=3,
            reranked_count=3,
            sources=["doc1.pdf"],
            scores=[0.95]
        )

        # Save MCP tool call
        store.save_mcp_tool_call(
            trace_id="trace-001",
            tool_name="web_search",
            start_time="2026-08-19T12:00:00Z",
            duration_ms=45.0,
            success=True,
            input_metadata={"query": "test"}
        )

        # Save evaluation result
        store.save_evaluation_result(
            trace_id="trace-001",
            overall_score=85,
            relevance_score=90,
            groundedness_score=80,
            completeness_score=85,
            safety_score=100,
            citation_quality=80,
            evaluator_type="heuristic",
            evaluation_latency_ms=2.0,
            evaluation_reason="Good response quality."
        )

        # Verify get_traces
        traces = store.get_traces(user_id="u1")
        assert len(traces) == 1
        assert traces[0]["trace_id"] == "trace-001"
        assert traces[0]["status"] == "success"

        # Verify get_trace_by_id
        detail = store.get_trace_by_id("trace-001", user_id="u1")
        assert detail is not None
        assert detail["trace_id"] == "trace-001"
        assert len(detail["agent_executions"]) == 1
        assert len(detail["llm_calls"]) == 1
        assert len(detail["retrieval_events"]) == 1
        assert len(detail["mcp_tool_calls"]) == 1
        assert len(detail["evaluation_results"]) == 1

        # Verify metrics
        obs_metrics = store.get_observability_metrics("u1")
        assert obs_metrics["total_requests"] == 1
        assert obs_metrics["success_rate"] == 1.0
        assert obs_metrics["total_tokens"] == 150
        assert obs_metrics["avg_evaluation_score"] == 85.0
    finally:
        gc.collect()
        try:
            os.remove(db_path)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_trace_agent_decorator():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = SQLiteConversationStore(db_path, namespace="test_ns")
        store.setup()
        set_persistence_store(store)

        set_trace_id("dec-trace-999")
        set_user_id("dec-user")

        @trace_agent("dummy_agent")
        async def dummy_node(state, config=None):
            return {"result": "ok"}

        out = await dummy_node({"query": "hi"})
        assert out == {"result": "ok"}

        detail = store.get_trace_by_id("dec-trace-999")
        assert detail is not None
        assert len(detail["agent_executions"]) == 1
        assert detail["agent_executions"][0]["agent_name"] == "dummy_agent"
    finally:
        gc.collect()
        try:
            os.remove(db_path)
        except Exception:
            pass
