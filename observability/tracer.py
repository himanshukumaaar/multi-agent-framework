import asyncio
import functools
import inspect
import time

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from observability.config import config
from observability.context import (
    get_trace_id, set_trace_id,
    get_user_id, set_user_id,
    get_thread_id, set_thread_id
)
from observability.redaction import sanitize_dict
from observability.metrics import (
    record_agent_execution_metric,
    record_llm_call_metric,
    record_rag_retrieval_metric,
    record_mcp_tool_metric,
    record_routing_metric
)

# Global in-memory collector store + hook for DB persistence
_persistence_store = None

def set_persistence_store(store: Any) -> None:
    global _persistence_store
    _persistence_store = store


class ObservabilityTracer:
    """Core telemetry and tracing collector."""

    @staticmethod
    def calculate_llm_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost based on model pricing config."""
        pricing = config.model_pricing.get(model_name.lower())
        if not pricing:
            # Check prefix match
            for k, (in_price, out_price) in config.model_pricing.items():
                if k in model_name.lower():
                    pricing = (in_price, out_price)
                    break
        if not pricing:
            return 0.0
        return (input_tokens * pricing[0]) + (output_tokens * pricing[1])

    @staticmethod
    def record_agent_execution(
        agent_name: str,
        trace_id: str,
        user_id: Optional[str],
        thread_id: Optional[str],
        start_time: str,
        end_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        route_selected: str = "",
        input_metadata: Optional[Dict[str, Any]] = None,
        output_metadata: Optional[Dict[str, Any]] = None,
        retry_count: int = 0
    ) -> None:
        if not config.enabled:
            return
        try:
            sanitized_input = sanitize_dict(input_metadata or {})
            sanitized_output = sanitize_dict(output_metadata or {})
            
            record_agent_execution_metric(
                agent_name=agent_name,
                duration_sec=duration_ms / 1000.0,
                success=success,
                error_type=error_type
            )

            if config.persistence_enabled and _persistence_store:
                if hasattr(_persistence_store, "save_agent_execution"):
                    _persistence_store.save_agent_execution(
                        trace_id=trace_id,
                        agent_name=agent_name,
                        user_id=user_id,
                        thread_id=thread_id,
                        start_time=start_time,
                        end_time=end_time,
                        duration_ms=duration_ms,
                        success=success,
                        error_type=error_type,
                        route_selected=route_selected,
                        input_metadata=sanitized_input,
                        output_metadata=sanitized_output,
                        retry_count=retry_count
                    )
        except Exception as e:
            print(f"[observability] Error recording agent execution: {e}")

    @staticmethod
    def record_llm_call(
        trace_id: str,
        agent_name: str,
        model_name: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        success: bool,
        error: str = ""
    ) -> float:
        if not config.enabled:
            return 0.0
        try:
            total_tokens = input_tokens + output_tokens
            cost = ObservabilityTracer.calculate_llm_cost(model_name, input_tokens, output_tokens)
            
            record_llm_call_metric(
                model=model_name,
                provider=provider,
                duration_sec=latency_ms / 1000.0,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=success
            )

            if config.persistence_enabled and _persistence_store:
                if hasattr(_persistence_store, "save_llm_call"):
                    _persistence_store.save_llm_call(
                        trace_id=trace_id,
                        agent_name=agent_name,
                        model_name=model_name,
                        provider=provider,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        latency_ms=latency_ms,
                        estimated_cost=cost,
                        success=success,
                        error=error
                    )
            return cost
        except Exception as e:
            print(f"[observability] Error recording LLM call: {e}")
            return 0.0

    @staticmethod
    def record_rag_event(
        trace_id: str,
        query: str,
        num_retrieved_docs: int,
        retrieval_latency_ms: float,
        reranking_latency_ms: float,
        top_k: int,
        retrieval_method: str,
        vector_count: int,
        bm25_count: int,
        reranked_count: int,
        sources: List[str],
        scores: List[float],
        evaluation_signals: Optional[Dict[str, Any]] = None
    ) -> None:
        if not config.enabled:
            return
        try:
            record_rag_retrieval_metric(
                method=retrieval_method,
                duration_sec=retrieval_latency_ms / 1000.0,
                success=num_retrieved_docs > 0 or "failed" not in retrieval_method.lower()
            )

            if config.persistence_enabled and _persistence_store:
                if hasattr(_persistence_store, "save_retrieval_event"):
                    _persistence_store.save_retrieval_event(
                        trace_id=trace_id,
                        query=query,
                        num_retrieved_docs=num_retrieved_docs,
                        retrieval_latency_ms=retrieval_latency_ms,
                        reranking_latency_ms=reranking_latency_ms,
                        top_k=top_k,
                        retrieval_method=retrieval_method,
                        vector_count=vector_count,
                        bm25_count=bm25_count,
                        reranked_count=reranked_count,
                        sources=sources,
                        scores=scores,
                        evaluation_signals=evaluation_signals or {}
                    )
        except Exception as e:
            print(f"[observability] Error recording RAG event: {e}")

    @staticmethod
    def record_mcp_tool_call(
        trace_id: str,
        tool_name: str,
        start_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        input_metadata: Optional[Dict[str, Any]] = None,
        output_metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        if not config.enabled:
            return
        try:
            sanitized_input = sanitize_dict(input_metadata or {})
            sanitized_output = sanitize_dict(output_metadata or {})

            record_mcp_tool_metric(
                tool_name=tool_name,
                duration_sec=duration_ms / 1000.0,
                success=success,
                error_type=error_type
            )

            if config.persistence_enabled and _persistence_store:
                if hasattr(_persistence_store, "save_mcp_tool_call"):
                    _persistence_store.save_mcp_tool_call(
                        trace_id=trace_id,
                        tool_name=tool_name,
                        start_time=start_time,
                        duration_ms=duration_ms,
                        success=success,
                        error_type=error_type,
                        input_metadata=sanitized_input,
                        output_metadata=sanitized_output
                    )
        except Exception as e:
            print(f"[observability] Error recording MCP tool call: {e}")

    @staticmethod
    def record_routing_decision(
        route: str,
        confidence: float,
        reason: str,
        routing_type: str = "deterministic"
    ) -> None:
        if not config.enabled:
            return
        try:
            record_routing_metric(route=route, routing_type=routing_type, confidence=confidence)
        except Exception as e:
            print(f"[observability] Error recording routing metric: {e}")


def trace_agent(agent_name: str):
    """
    Decorator for agent functions in LangGraph to automatically capture execution time,
    success/failure, inputs, outputs, and trace_id propagation.
    """
    def decorator(func: Callable):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapper(state: Dict[str, Any], config_param: Any = None, **kwargs):
                trace_id = (state.get("trace_id") or get_trace_id())
                user_id = get_user_id()
                thread_id = get_thread_id()

                start_dt = datetime.now(timezone.utc)
                start_str = start_dt.isoformat()
                t0 = time.perf_counter()

                success = True
                error_type = ""
                output_state = {}

                try:
                    if config_param is not None:
                        output_state = await func(state, config_param, **kwargs)
                    else:
                        output_state = await func(state, **kwargs)
                    return output_state
                except Exception as e:
                    success = False
                    error_type = type(e).__name__
                    raise e
                finally:
                    t1 = time.perf_counter()
                    duration_ms = (t1 - t0) * 1000.0
                    end_str = datetime.now(timezone.utc).isoformat()

                    route_selected = ""
                    if isinstance(output_state, dict):
                        route_selected = output_state.get("route") or state.get("route") or ""
                    elif isinstance(state, dict):
                        route_selected = state.get("route") or ""

                    # Extract query summary for input meta
                    query = (state.get("query") or state.get("rewritten_query") or "").strip()
                    input_meta = {"query_len": len(query), "messages_count": len(state.get("messages", []))}
                    output_meta = {}
                    if isinstance(output_state, dict):
                        for k in ("route", "evaluation_score", "web_hitl_decision"):
                            if k in output_state:
                                output_meta[k] = output_state[k]

                    ObservabilityTracer.record_agent_execution(
                        agent_name=agent_name,
                        trace_id=trace_id,
                        user_id=user_id,
                        thread_id=thread_id,
                        start_time=start_str,
                        end_time=end_str,
                        duration_ms=duration_ms,
                        success=success,
                        error_type=error_type,
                        route_selected=route_selected,
                        input_metadata=input_meta,
                        output_metadata=output_meta
                    )

            return wrapper
        else:
            @functools.wraps(func)
            def wrapper(state: Dict[str, Any], config_param: Any = None, **kwargs):
                trace_id = (state.get("trace_id") or get_trace_id())
                user_id = get_user_id()
                thread_id = get_thread_id()

                start_dt = datetime.now(timezone.utc)
                start_str = start_dt.isoformat()
                t0 = time.perf_counter()

                success = True
                error_type = ""
                output_state = {}

                try:
                    if config_param is not None:
                        output_state = func(state, config_param, **kwargs)
                    else:
                        output_state = func(state, **kwargs)
                    return output_state
                except Exception as e:
                    success = False
                    error_type = type(e).__name__
                    raise e
                finally:
                    t1 = time.perf_counter()
                    duration_ms = (t1 - t0) * 1000.0
                    end_str = datetime.now(timezone.utc).isoformat()

                    route_selected = ""
                    if isinstance(output_state, dict):
                        route_selected = output_state.get("route") or state.get("route") or ""
                    elif isinstance(state, dict):
                        route_selected = state.get("route") or ""

                    query = (state.get("query") or state.get("rewritten_query") or "").strip()
                    input_meta = {"query_len": len(query), "messages_count": len(state.get("messages", []))}
                    output_meta = {}
                    if isinstance(output_state, dict):
                        for k in ("route", "evaluation_score", "web_hitl_decision"):
                            if k in output_state:
                                output_meta[k] = output_state[k]

                    ObservabilityTracer.record_agent_execution(
                        agent_name=agent_name,
                        trace_id=trace_id,
                        user_id=user_id,
                        thread_id=thread_id,
                        start_time=start_str,
                        end_time=end_str,
                        duration_ms=duration_ms,
                        success=success,
                        error_type=error_type,
                        route_selected=route_selected,
                        input_metadata=input_meta,
                        output_metadata=output_meta
                    )

            return wrapper

    return decorator
