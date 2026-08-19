from prometheus_client import Counter, Histogram

AGENT_EXECUTION_TOTAL = Counter(
    "agent_execution_total",
    "Total agent node executions",
    ["agent_name", "status"],
)

AGENT_EXECUTION_DURATION_SECONDS = Histogram(
    "agent_execution_duration_seconds",
    "Agent execution duration in seconds",
    ["agent_name"],
)

AGENT_EXECUTION_ERRORS_TOTAL = Counter(
    "agent_execution_errors_total",
    "Total agent execution errors",
    ["agent_name", "error_type"],
)

LLM_REQUESTS_TOTAL = Counter(
    "llm_requests_total",
    "Total LLM calls",
    ["model", "provider", "status"],
)

LLM_REQUEST_DURATION_SECONDS = Histogram(
    "llm_request_duration_seconds",
    "LLM call duration in seconds",
    ["model", "provider"],
)

LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Total LLM tokens used",
    ["model", "token_type"],  # token_type: input, output, total
)

RAG_RETRIEVAL_TOTAL = Counter(
    "rag_retrieval_total",
    "Total RAG retrievals",
    ["method", "status"],
)

RAG_RETRIEVAL_DURATION_SECONDS = Histogram(
    "rag_retrieval_duration_seconds",
    "RAG retrieval duration in seconds",
    ["method"],
)

MCP_TOOL_CALLS_TOTAL = Counter(
    "mcp_tool_calls_total",
    "Total MCP tool invocations",
    ["tool_name", "status"],
)

MCP_TOOL_DURATION_SECONDS = Histogram(
    "mcp_tool_duration_seconds",
    "MCP tool duration in seconds",
    ["tool_name"],
)

MCP_TOOL_ERRORS_TOTAL = Counter(
    "mcp_tool_errors_total",
    "Total MCP tool errors",
    ["tool_name", "error_type"],
)

EVALUATION_TOTAL = Counter(
    "evaluation_total",
    "Total response evaluations performed",
    ["evaluator_type"],
)

EVALUATION_SCORE = Histogram(
    "evaluation_score",
    "Distribution of response evaluation scores",
    ["metric_name"],  # overall, relevance, groundedness, completeness, safety
)

ROUTING_TOTAL = Counter(
    "routing_total",
    "Total intent routing decisions",
    ["route", "routing_type"],  # deterministic vs llm_fallback vs control
)

ROUTING_CONFIDENCE = Histogram(
    "routing_confidence",
    "Distribution of intent routing confidence scores",
    ["route"],
)


def record_agent_execution_metric(agent_name: str, duration_sec: float, success: bool, error_type: str = "") -> None:
    try:
        status = "success" if success else "failure"
        AGENT_EXECUTION_TOTAL.labels(agent_name=agent_name, status=status).inc()
        AGENT_EXECUTION_DURATION_SECONDS.labels(agent_name=agent_name).observe(duration_sec)
        if not success and error_type:
            AGENT_EXECUTION_ERRORS_TOTAL.labels(agent_name=agent_name, error_type=error_type[:64]).inc()
    except Exception:
        pass


def record_llm_call_metric(model: str, provider: str, duration_sec: float, input_tokens: int, output_tokens: int, success: bool) -> None:
    try:
        status = "success" if success else "failure"
        LLM_REQUESTS_TOTAL.labels(model=model, provider=provider, status=status).inc()
        LLM_REQUEST_DURATION_SECONDS.labels(model=model, provider=provider).observe(duration_sec)
        if success:
            if input_tokens > 0:
                LLM_TOKENS_TOTAL.labels(model=model, token_type="input").inc(input_tokens)
            if output_tokens > 0:
                LLM_TOKENS_TOTAL.labels(model=model, token_type="output").inc(output_tokens)
            if (input_tokens + output_tokens) > 0:
                LLM_TOKENS_TOTAL.labels(model=model, token_type="total").inc(input_tokens + output_tokens)
    except Exception:
        pass


def record_rag_retrieval_metric(method: str, duration_sec: float, success: bool) -> None:
    try:
        status = "success" if success else "failure"
        RAG_RETRIEVAL_TOTAL.labels(method=method, status=status).inc()
        RAG_RETRIEVAL_DURATION_SECONDS.labels(method=method).observe(duration_sec)
    except Exception:
        pass


def record_mcp_tool_metric(tool_name: str, duration_sec: float, success: bool, error_type: str = "") -> None:
    try:
        status = "success" if success else "failure"
        MCP_TOOL_CALLS_TOTAL.labels(tool_name=tool_name, status=status).inc()
        MCP_TOOL_DURATION_SECONDS.labels(tool_name=tool_name).observe(duration_sec)
        if not success and error_type:
            MCP_TOOL_ERRORS_TOTAL.labels(tool_name=tool_name, error_type=error_type[:64]).inc()
    except Exception:
        pass


def record_evaluation_metric(evaluator_type: str, overall: float, relevance: float, groundedness: float, completeness: float, safety: float) -> None:
    try:
        EVALUATION_TOTAL.labels(evaluator_type=evaluator_type).inc()
        EVALUATION_SCORE.labels(metric_name="overall").observe(overall)
        EVALUATION_SCORE.labels(metric_name="relevance").observe(relevance)
        EVALUATION_SCORE.labels(metric_name="groundedness").observe(groundedness)
        EVALUATION_SCORE.labels(metric_name="completeness").observe(completeness)
        EVALUATION_SCORE.labels(metric_name="safety").observe(safety)
    except Exception:
        pass


def record_routing_metric(route: str, routing_type: str, confidence: float) -> None:
    try:
        ROUTING_TOTAL.labels(route=route, routing_type=routing_type).inc()
        ROUTING_CONFIDENCE.labels(route=route).observe(confidence)
    except Exception:
        pass
