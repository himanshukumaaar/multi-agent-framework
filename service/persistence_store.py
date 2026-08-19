import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class StoreOpenResult:
    store: "BaseConversationStore"
    backend_label: str


class BaseConversationStore:
    def setup(self) -> None:
        raise NotImplementedError

    def save_message(
        self,
        user_id: str | None,
        thread_id: str,
        run_id: str,
        role: str,
        content: str,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    def list_messages(
        self,
        thread_id: str,
        limit: int = 50,
        user_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def list_threads(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def create_user(self, user_id: str, password_hash: str) -> bool:
        raise NotImplementedError

    def get_user_password_hash(self, user_id: str) -> str | None:
        raise NotImplementedError

    def save_hitl_event(
        self,
        user_id: str | None,
        thread_id: str | None,
        query: str,
        decision: str,
        reason: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    def list_hitl_events(
        self,
        user_id: str,
        limit: int = 50,
        thread_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def save_trace(
        self,
        trace_id: str,
        user_id: str | None,
        thread_id: str | None,
        status: str = "running",
        duration_ms: float = 0.0,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        pass

    def save_agent_execution(
        self,
        trace_id: str,
        agent_name: str,
        user_id: str | None,
        thread_id: str | None,
        start_time: str,
        end_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        route_selected: str = "",
        input_metadata: Dict[str, Any] | None = None,
        output_metadata: Dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> None:
        pass

    def save_llm_call(
        self,
        trace_id: str,
        agent_name: str,
        model_name: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: float,
        estimated_cost: float,
        success: bool,
        error: str = "",
    ) -> None:
        pass

    def save_retrieval_event(
        self,
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
        evaluation_signals: Dict[str, Any] | None = None,
    ) -> None:
        pass

    def save_mcp_tool_call(
        self,
        trace_id: str,
        tool_name: str,
        start_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        input_metadata: Dict[str, Any] | None = None,
        output_metadata: Dict[str, Any] | None = None,
    ) -> None:
        pass

    def save_evaluation_result(
        self,
        trace_id: str,
        overall_score: int,
        relevance_score: int,
        groundedness_score: int,
        completeness_score: int,
        safety_score: int,
        citation_quality: int,
        evaluator_type: str,
        evaluation_latency_ms: float,
        evaluation_reason: str,
    ) -> None:
        pass

    def get_traces(
        self,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> List[Dict[str, Any]]:
        return []

    def get_trace_by_id(
        self,
        trace_id: str,
        user_id: str | None = None,
    ) -> Dict[str, Any] | None:
        return None

    def get_observability_metrics(self, user_id: str | None = None) -> Dict[str, Any]:
        return {}

    def get_agent_metrics(self, user_id: str | None = None) -> List[Dict[str, Any]]:
        return []

    def get_tool_metrics(self, user_id: str | None = None) -> List[Dict[str, Any]]:
        return []

    def get_evaluations(
        self,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        return []



class SQLiteConversationStore(BaseConversationStore):
    def __init__(self, db_path: str, namespace: str = "default") -> None:
        self.db_path = db_path
        self.namespace = namespace

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        add_column_ddl: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        columns = {row["name"] for row in rows}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {add_column_ddl}")

    def setup(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_store (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Backward-compat migration for pre-user-id schema.
            self._ensure_column(conn, "conversation_store", "user_id", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_store_thread
                ON conversation_store(namespace, thread_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_store_thread_user
                ON conversation_store(namespace, user_id, thread_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(namespace, user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hitl_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    query TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hitl_events_user
                ON hitl_events(namespace, user_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hitl_events_thread
                ON hitl_events(namespace, user_id, thread_id, created_at DESC)
                """
            )
            # Observability tables
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'running',
                    duration_ms REAL DEFAULT 0.0,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_traces_user
                ON traces(namespace, user_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    start_time TEXT,
                    end_time TEXT,
                    duration_ms REAL DEFAULT 0.0,
                    success INTEGER NOT NULL DEFAULT 1,
                    error_type TEXT DEFAULT '',
                    route_selected TEXT DEFAULT '',
                    input_metadata TEXT,
                    output_metadata TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_executions_trace
                ON agent_executions(namespace, trace_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    latency_ms REAL DEFAULT 0.0,
                    estimated_cost REAL DEFAULT 0.0,
                    success INTEGER NOT NULL DEFAULT 1,
                    error TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retrieval_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    num_retrieved_docs INTEGER DEFAULT 0,
                    retrieval_latency_ms REAL DEFAULT 0.0,
                    reranking_latency_ms REAL DEFAULT 0.0,
                    top_k INTEGER DEFAULT 0,
                    retrieval_method TEXT DEFAULT 'vector',
                    vector_count INTEGER DEFAULT 0,
                    bm25_count INTEGER DEFAULT 0,
                    reranked_count INTEGER DEFAULT 0,
                    sources TEXT,
                    scores TEXT,
                    evaluation_signals TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    start_time TEXT,
                    duration_ms REAL DEFAULT 0.0,
                    success INTEGER NOT NULL DEFAULT 1,
                    error_type TEXT DEFAULT '',
                    input_metadata TEXT,
                    output_metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    overall_score INTEGER DEFAULT 0,
                    relevance_score INTEGER DEFAULT 0,
                    groundedness_score INTEGER DEFAULT 0,
                    completeness_score INTEGER DEFAULT 0,
                    safety_score INTEGER DEFAULT 0,
                    citation_quality INTEGER DEFAULT 0,
                    evaluator_type TEXT DEFAULT 'heuristic',
                    evaluation_latency_ms REAL DEFAULT 0.0,
                    evaluation_reason TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def save_message(
        self,
        user_id: str | None,
        thread_id: str,
        run_id: str,
        role: str,
        content: str,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        clean_content = (content or "").strip()
        if not clean_content:
            return
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_store (namespace, user_id, thread_id, run_id, role, content, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.namespace,
                    clean_user_id,
                    thread_id,
                    run_id,
                    role,
                    clean_content,
                    json.dumps(metadata or {}),
                ),
            )
            conn.commit()

    def list_messages(
        self,
        thread_id: str,
        limit: int = 50,
        user_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 200))
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            if clean_user_id:
                rows = conn.execute(
                    """
                    SELECT user_id, thread_id, run_id, role, content, metadata, created_at
                    FROM conversation_store
                    WHERE namespace = ? AND user_id = ? AND thread_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (self.namespace, clean_user_id, thread_id, bounded_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT user_id, thread_id, run_id, role, content, metadata, created_at
                    FROM conversation_store
                    WHERE namespace = ? AND thread_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (self.namespace, thread_id, bounded_limit),
                ).fetchall()
        output: List[Dict[str, Any]] = []
        for row in rows:
            try:
                parsed_meta = json.loads(row["metadata"] or "{}")
            except Exception:
                parsed_meta = {}
            output.append(
                {
                    "user_id": row["user_id"] or "",
                    "thread_id": row["thread_id"],
                    "run_id": row["run_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "metadata": parsed_meta,
                    "created_at": row["created_at"],
                }
            )
        return output

    def list_threads(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            return []
        bounded_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT thread_id, MAX(created_at) AS last_message_at, COUNT(*) AS message_count
                FROM conversation_store
                WHERE namespace = ? AND user_id = ?
                GROUP BY thread_id
                ORDER BY last_message_at DESC
                LIMIT ?
                """,
                (self.namespace, clean_user_id, bounded_limit),
            ).fetchall()

            output: List[Dict[str, Any]] = []
            for row in rows:
                preview_row = conn.execute(
                    """
                    SELECT content
                    FROM conversation_store
                    WHERE namespace = ? AND user_id = ? AND thread_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (self.namespace, clean_user_id, row["thread_id"]),
                ).fetchone()
                output.append(
                    {
                        "thread_id": row["thread_id"],
                        "message_count": int(row["message_count"] or 0),
                        "last_message_at": row["last_message_at"],
                        "last_message_preview": (preview_row["content"] if preview_row else "") or "",
                    }
                )
        return output

    def create_user(self, user_id: str, password_hash: str) -> bool:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            raise ValueError("user_id is required")
        if not password_hash:
            raise ValueError("password_hash is required")
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (namespace, user_id, password_hash)
                    VALUES (?, ?, ?)
                    """,
                    (self.namespace, clean_user_id, password_hash),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_user_password_hash(self, user_id: str) -> str | None:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT password_hash
                FROM users
                WHERE namespace = ? AND user_id = ?
                LIMIT 1
                """,
                (self.namespace, clean_user_id),
            ).fetchone()
        if not row:
            return None
        return str(row["password_hash"] or "")

    def save_hitl_event(
        self,
        user_id: str | None,
        thread_id: str | None,
        query: str,
        decision: str,
        reason: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        clean_query = (query or "").strip()
        clean_decision = (decision or "").strip().lower()
        if not clean_query or clean_decision not in {"approved", "rejected"}:
            return
        clean_user_id = (user_id or "").strip()
        clean_thread_id = (thread_id or "").strip()
        clean_reason = (reason or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hitl_events (namespace, user_id, thread_id, query, decision, reason, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.namespace,
                    clean_user_id,
                    clean_thread_id,
                    clean_query,
                    clean_decision,
                    clean_reason,
                    json.dumps(metadata or {}),
                ),
            )
            conn.commit()

    def list_hitl_events(
        self,
        user_id: str,
        limit: int = 50,
        thread_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            return []
        clean_thread_id = (thread_id or "").strip()
        bounded_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            if clean_thread_id:
                rows = conn.execute(
                    """
                    SELECT user_id, thread_id, query, decision, reason, metadata, created_at
                    FROM hitl_events
                    WHERE namespace = ? AND user_id = ? AND thread_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (self.namespace, clean_user_id, clean_thread_id, bounded_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT user_id, thread_id, query, decision, reason, metadata, created_at
                    FROM hitl_events
                    WHERE namespace = ? AND user_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (self.namespace, clean_user_id, bounded_limit),
                ).fetchall()

        output: List[Dict[str, Any]] = []
        for row in rows:
            try:
                parsed_meta = json.loads(row["metadata"] or "{}")
            except Exception:
                parsed_meta = {}
            output.append(
                {
                    "user_id": row["user_id"] or "",
                    "thread_id": row["thread_id"] or "",
                    "query": row["query"],
                    "decision": row["decision"],
                    "reason": row["reason"] or "",
                    "metadata": parsed_meta,
                    "created_at": row["created_at"],
                }
            )
        return output

    def save_trace(
        self,
        trace_id: str,
        user_id: str | None,
        thread_id: str | None,
        status: str = "running",
        duration_ms: float = 0.0,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        clean_user_id = (user_id or "").strip()
        clean_thread_id = (thread_id or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO traces (trace_id, namespace, user_id, thread_id, status, duration_ms, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    self.namespace,
                    clean_user_id,
                    clean_thread_id,
                    status,
                    duration_ms,
                    json.dumps(metadata or {}),
                ),
            )
            conn.commit()

    def save_agent_execution(
        self,
        trace_id: str,
        agent_name: str,
        user_id: str | None,
        thread_id: str | None,
        start_time: str,
        end_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        route_selected: str = "",
        input_metadata: Dict[str, Any] | None = None,
        output_metadata: Dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> None:
        clean_user_id = (user_id or "").strip()
        clean_thread_id = (thread_id or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_executions (
                    namespace, trace_id, agent_name, user_id, thread_id,
                    start_time, end_time, duration_ms, success, error_type,
                    route_selected, input_metadata, output_metadata, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.namespace, trace_id, agent_name, clean_user_id, clean_thread_id,
                    start_time, end_time, duration_ms, 1 if success else 0, error_type,
                    route_selected, json.dumps(input_metadata or {}), json.dumps(output_metadata or {}), retry_count
                ),
            )
            conn.execute(
                """
                INSERT INTO traces (trace_id, namespace, user_id, thread_id, status, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    duration_ms = duration_ms + excluded.duration_ms,
                    status = CASE WHEN excluded.status = 'failed' THEN 'failed' ELSE status END
                """,
                (trace_id, self.namespace, clean_user_id, clean_thread_id, "completed" if success else "failed", duration_ms)
            )
            conn.commit()

    def save_llm_call(
        self,
        trace_id: str,
        agent_name: str,
        model_name: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: float,
        estimated_cost: float,
        success: bool,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls (
                    namespace, trace_id, agent_name, model_name, provider,
                    input_tokens, output_tokens, total_tokens, latency_ms,
                    estimated_cost, success, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.namespace, trace_id, agent_name, model_name, provider,
                    input_tokens, output_tokens, total_tokens, latency_ms,
                    estimated_cost, 1 if success else 0, error
                ),
            )
            conn.commit()

    def save_retrieval_event(
        self,
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
        evaluation_signals: Dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO retrieval_events (
                    namespace, trace_id, query, num_retrieved_docs,
                    retrieval_latency_ms, reranking_latency_ms, top_k,
                    retrieval_method, vector_count, bm25_count, reranked_count,
                    sources, scores, evaluation_signals
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.namespace, trace_id, query, num_retrieved_docs,
                    retrieval_latency_ms, reranking_latency_ms, top_k,
                    retrieval_method, vector_count, bm25_count, reranked_count,
                    json.dumps(sources or []), json.dumps(scores or []),
                    json.dumps(evaluation_signals or {})
                ),
            )
            conn.commit()

    def save_mcp_tool_call(
        self,
        trace_id: str,
        tool_name: str,
        start_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        input_metadata: Dict[str, Any] | None = None,
        output_metadata: Dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mcp_tool_calls (
                    namespace, trace_id, tool_name, start_time, duration_ms,
                    success, error_type, input_metadata, output_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.namespace, trace_id, tool_name, start_time, duration_ms,
                    1 if success else 0, error_type,
                    json.dumps(input_metadata or {}), json.dumps(output_metadata or {})
                ),
            )
            conn.commit()

    def save_evaluation_result(
        self,
        trace_id: str,
        overall_score: int,
        relevance_score: int,
        groundedness_score: int,
        completeness_score: int,
        safety_score: int,
        citation_quality: int,
        evaluator_type: str,
        evaluation_latency_ms: float,
        evaluation_reason: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_results (
                    namespace, trace_id, overall_score, relevance_score,
                    groundedness_score, completeness_score, safety_score,
                    citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.namespace, trace_id, overall_score, relevance_score,
                    groundedness_score, completeness_score, safety_score,
                    citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason
                ),
            )
            conn.commit()

    def get_traces(
        self,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> List[Dict[str, Any]]:
        clean_user_id = (user_id or "").strip()
        bounded_limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        sql = "SELECT trace_id, user_id, thread_id, status, duration_ms, metadata, created_at FROM traces WHERE namespace = ?"
        params: list[Any] = [self.namespace]
        if clean_user_id:
            sql += " AND user_id = ?"
            params.append(clean_user_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([bounded_limit, offset])

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        traces = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"] or "{}")
            except Exception:
                meta = {}
            traces.append({
                "trace_id": r["trace_id"],
                "user_id": r["user_id"],
                "thread_id": r["thread_id"],
                "status": r["status"],
                "duration_ms": r["duration_ms"],
                "metadata": meta,
                "created_at": r["created_at"]
            })
        return traces

    def get_trace_by_id(
        self,
        trace_id: str,
        user_id: str | None = None,
    ) -> Dict[str, Any] | None:
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            sql = "SELECT trace_id, user_id, thread_id, status, duration_ms, metadata, created_at FROM traces WHERE namespace = ? AND trace_id = ?"
            params: list[Any] = [self.namespace, trace_id]
            if clean_user_id:
                sql += " AND user_id = ?"
                params.append(clean_user_id)
            row = conn.execute(sql, params).fetchone()
            if not row:
                return None

            try:
                meta = json.loads(row["metadata"] or "{}")
            except Exception:
                meta = {}

            trace = {
                "trace_id": row["trace_id"],
                "user_id": row["user_id"],
                "thread_id": row["thread_id"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "metadata": meta,
                "created_at": row["created_at"],
                "agent_executions": [],
                "llm_calls": [],
                "retrieval_events": [],
                "mcp_tool_calls": [],
                "evaluation_results": []
            }

            exec_rows = conn.execute(
                "SELECT agent_name, start_time, end_time, duration_ms, success, error_type, route_selected, input_metadata, output_metadata FROM agent_executions WHERE namespace = ? AND trace_id = ? ORDER BY id ASC",
                (self.namespace, trace_id)
            ).fetchall()
            for er in exec_rows:
                try: im = json.loads(er["input_metadata"] or "{}")
                except Exception: im = {}
                try: om = json.loads(er["output_metadata"] or "{}")
                except Exception: om = {}
                trace["agent_executions"].append({
                    "agent_name": er["agent_name"],
                    "start_time": er["start_time"],
                    "end_time": er["end_time"],
                    "duration_ms": er["duration_ms"],
                    "success": bool(er["success"]),
                    "error_type": er["error_type"],
                    "route_selected": er["route_selected"],
                    "input_metadata": im,
                    "output_metadata": om,
                })

            llm_rows = conn.execute(
                "SELECT agent_name, model_name, provider, input_tokens, output_tokens, total_tokens, latency_ms, estimated_cost, success, error FROM llm_calls WHERE namespace = ? AND trace_id = ? ORDER BY id ASC",
                (self.namespace, trace_id)
            ).fetchall()
            for lr in llm_rows:
                trace["llm_calls"].append({
                    "agent_name": lr["agent_name"],
                    "model_name": lr["model_name"],
                    "provider": lr["provider"],
                    "input_tokens": lr["input_tokens"],
                    "output_tokens": lr["output_tokens"],
                    "total_tokens": lr["total_tokens"],
                    "latency_ms": lr["latency_ms"],
                    "estimated_cost": lr["estimated_cost"],
                    "success": bool(lr["success"]),
                    "error": lr["error"]
                })

            ret_rows = conn.execute(
                "SELECT query, num_retrieved_docs, retrieval_latency_ms, reranking_latency_ms, top_k, retrieval_method, vector_count, bm25_count, reranked_count, sources, scores, evaluation_signals FROM retrieval_events WHERE namespace = ? AND trace_id = ? ORDER BY id ASC",
                (self.namespace, trace_id)
            ).fetchall()
            for rr in ret_rows:
                try: s = json.loads(rr["sources"] or "[]")
                except Exception: s = []
                try: sc = json.loads(rr["scores"] or "[]")
                except Exception: sc = []
                try: sig = json.loads(rr["evaluation_signals"] or "{}")
                except Exception: sig = {}
                trace["retrieval_events"].append({
                    "query": rr["query"],
                    "num_retrieved_docs": rr["num_retrieved_docs"],
                    "retrieval_latency_ms": rr["retrieval_latency_ms"],
                    "reranking_latency_ms": rr["reranking_latency_ms"],
                    "top_k": rr["top_k"],
                    "retrieval_method": rr["retrieval_method"],
                    "vector_count": rr["vector_count"],
                    "bm25_count": rr["bm25_count"],
                    "reranked_count": rr["reranked_count"],
                    "sources": s,
                    "scores": sc,
                    "evaluation_signals": sig
                })

            mcp_rows = conn.execute(
                "SELECT tool_name, start_time, duration_ms, success, error_type, input_metadata, output_metadata FROM mcp_tool_calls WHERE namespace = ? AND trace_id = ? ORDER BY id ASC",
                (self.namespace, trace_id)
            ).fetchall()
            for mr in mcp_rows:
                try: im = json.loads(mr["input_metadata"] or "{}")
                except Exception: im = {}
                try: om = json.loads(mr["output_metadata"] or "{}")
                except Exception: om = {}
                trace["mcp_tool_calls"].append({
                    "tool_name": mr["tool_name"],
                    "start_time": mr["start_time"],
                    "duration_ms": mr["duration_ms"],
                    "success": bool(mr["success"]),
                    "error_type": mr["error_type"],
                    "input_metadata": im,
                    "output_metadata": om,
                })

            eval_rows = conn.execute(
                "SELECT overall_score, relevance_score, groundedness_score, completeness_score, safety_score, citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason FROM evaluation_results WHERE namespace = ? AND trace_id = ? ORDER BY id ASC",
                (self.namespace, trace_id)
            ).fetchall()
            for ev in eval_rows:
                trace["evaluation_results"].append({
                    "overall_score": ev["overall_score"],
                    "relevance_score": ev["relevance_score"],
                    "groundedness_score": ev["groundedness_score"],
                    "completeness_score": ev["completeness_score"],
                    "safety_score": ev["safety_score"],
                    "citation_quality": ev["citation_quality"],
                    "evaluator_type": ev["evaluator_type"],
                    "evaluation_latency_ms": ev["evaluation_latency_ms"],
                    "evaluation_reason": ev["evaluation_reason"]
                })

        return trace

    def get_observability_metrics(self, user_id: str | None = None) -> Dict[str, Any]:
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            where_clause = "WHERE namespace = ?"
            params: list[Any] = [self.namespace]
            if clean_user_id:
                where_clause += " AND user_id = ?"
                params.append(clean_user_id)

            t_row = conn.execute(
                f"SELECT COUNT(*) as total_requests, SUM(CASE WHEN status != 'failed' THEN 1 ELSE 0 END) as success_count, AVG(duration_ms) as avg_latency FROM traces {where_clause}",
                params
            ).fetchone()
            total_requests = t_row["total_requests"] or 0
            success_count = t_row["success_count"] or 0
            avg_latency = float(t_row["avg_latency"] or 0.0)
            success_rate = (success_count / total_requests) if total_requests > 0 else 1.0
            error_rate = 1.0 - success_rate

            llm_row = conn.execute(
                f"SELECT COUNT(*) as total_calls, SUM(total_tokens) as total_tokens, SUM(estimated_cost) as total_cost, AVG(latency_ms) as avg_llm_latency FROM llm_calls WHERE namespace = ?",
                [self.namespace]
            ).fetchone()
            total_llm_calls = llm_row["total_calls"] or 0
            total_tokens = llm_row["total_tokens"] or 0
            estimated_cost = float(llm_row["total_cost"] or 0.0)
            avg_llm_latency = float(llm_row["avg_llm_latency"] or 0.0)

            eval_row = conn.execute(
                f"SELECT AVG(overall_score) as avg_score, SUM(CASE WHEN overall_score < 60 THEN 1 ELSE 0 END) as low_quality_count FROM evaluation_results WHERE namespace = ?",
                [self.namespace]
            ).fetchone()
            avg_eval_score = float(eval_row["avg_score"] or 0.0)
            low_quality_count = eval_row["low_quality_count"] or 0

            return {
                "total_requests": total_requests,
                "success_rate": round(success_rate, 4),
                "error_rate": round(error_rate, 4),
                "avg_latency_ms": round(avg_latency, 2),
                "p95_latency_ms": round(avg_latency * 1.3, 2),
                "total_llm_calls": total_llm_calls,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(estimated_cost, 6),
                "avg_llm_latency_ms": round(avg_llm_latency, 2),
                "avg_evaluation_score": round(avg_eval_score, 2),
                "low_quality_count": low_quality_count
            }

    def get_agent_metrics(self, user_id: str | None = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT agent_name, COUNT(*) as count, AVG(duration_ms) as avg_duration_ms, SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures FROM agent_executions WHERE namespace = ? GROUP BY agent_name ORDER BY count DESC",
                [self.namespace]
            ).fetchall()
            metrics = []
            for r in rows:
                cnt = r["count"] or 0
                fails = r["failures"] or 0
                metrics.append({
                    "agent_name": r["agent_name"],
                    "executions": cnt,
                    "avg_duration_ms": round(float(r["avg_duration_ms"] or 0.0), 2),
                    "failures": fails,
                    "failure_rate": round((fails / cnt) if cnt > 0 else 0.0, 4)
                })
            return metrics

    def get_tool_metrics(self, user_id: str | None = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tool_name, COUNT(*) as count, AVG(duration_ms) as avg_duration_ms, SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures FROM mcp_tool_calls WHERE namespace = ? GROUP BY tool_name ORDER BY count DESC",
                [self.namespace]
            ).fetchall()
            metrics = []
            for r in rows:
                cnt = r["count"] or 0
                fails = r["failures"] or 0
                metrics.append({
                    "tool_name": r["tool_name"],
                    "calls": cnt,
                    "avg_duration_ms": round(float(r["avg_duration_ms"] or 0.0), 2),
                    "failures": fails,
                    "failure_rate": round((fails / cnt) if cnt > 0 else 0.0, 4)
                })
            return metrics

    def get_evaluations(
        self,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT trace_id, overall_score, relevance_score, groundedness_score, completeness_score, safety_score, citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason, created_at FROM evaluation_results WHERE namespace = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [self.namespace, bounded_limit, offset]
            ).fetchall()
            evals = []
            for r in rows:
                evals.append({
                    "trace_id": r["trace_id"],
                    "overall_score": r["overall_score"],
                    "relevance_score": r["relevance_score"],
                    "groundedness_score": r["groundedness_score"],
                    "completeness_score": r["completeness_score"],
                    "safety_score": r["safety_score"],
                    "citation_quality": r["citation_quality"],
                    "evaluator_type": r["evaluator_type"],
                    "evaluation_latency_ms": r["evaluation_latency_ms"],
                    "evaluation_reason": r["evaluation_reason"],
                    "created_at": r["created_at"]
                })
            return evals


class PostgresConversationStore(BaseConversationStore):
    def __init__(self, conn_string: str, namespace: str = "default") -> None:
        self.conn_string = conn_string
        self.namespace = namespace

    def _connect(self):
        import psycopg

        return psycopg.connect(self.conn_string, autocommit=True)

    def setup(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_store (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT '',
                        thread_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata JSONB,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE conversation_store
                    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_conversation_store_thread
                    ON conversation_store(namespace, thread_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_conversation_store_thread_user
                    ON conversation_store(namespace, user_id, thread_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        UNIQUE(namespace, user_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS hitl_events (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT '',
                        thread_id TEXT NOT NULL DEFAULT '',
                        query TEXT NOT NULL,
                        decision TEXT NOT NULL,
                        reason TEXT,
                        metadata JSONB,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_hitl_events_user
                    ON hitl_events(namespace, user_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_hitl_events_thread
                    ON hitl_events(namespace, user_id, thread_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS traces (
                        trace_id TEXT PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT '',
                        thread_id TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'running',
                        duration_ms DOUBLE PRECISION DEFAULT 0.0,
                        metadata JSONB,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_traces_user
                    ON traces(namespace, user_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_executions (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        agent_name TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT '',
                        thread_id TEXT NOT NULL DEFAULT '',
                        start_time TEXT,
                        end_time TEXT,
                        duration_ms DOUBLE PRECISION DEFAULT 0.0,
                        success BOOLEAN NOT NULL DEFAULT TRUE,
                        error_type TEXT DEFAULT '',
                        route_selected TEXT DEFAULT '',
                        input_metadata JSONB,
                        output_metadata JSONB,
                        retry_count INT DEFAULT 0,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_calls (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        agent_name TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        input_tokens INT DEFAULT 0,
                        output_tokens INT DEFAULT 0,
                        total_tokens INT DEFAULT 0,
                        latency_ms DOUBLE PRECISION DEFAULT 0.0,
                        estimated_cost DOUBLE PRECISION DEFAULT 0.0,
                        success BOOLEAN NOT NULL DEFAULT TRUE,
                        error TEXT DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS retrieval_events (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        query TEXT NOT NULL,
                        num_retrieved_docs INT DEFAULT 0,
                        retrieval_latency_ms DOUBLE PRECISION DEFAULT 0.0,
                        reranking_latency_ms DOUBLE PRECISION DEFAULT 0.0,
                        top_k INT DEFAULT 0,
                        retrieval_method TEXT DEFAULT 'vector',
                        vector_count INT DEFAULT 0,
                        bm25_count INT DEFAULT 0,
                        reranked_count INT DEFAULT 0,
                        sources JSONB,
                        scores JSONB,
                        evaluation_signals JSONB,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mcp_tool_calls (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        start_time TEXT,
                        duration_ms DOUBLE PRECISION DEFAULT 0.0,
                        success BOOLEAN NOT NULL DEFAULT TRUE,
                        error_type TEXT DEFAULT '',
                        input_metadata JSONB,
                        output_metadata JSONB,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evaluation_results (
                        id BIGSERIAL PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        trace_id TEXT NOT NULL,
                        overall_score INT DEFAULT 0,
                        relevance_score INT DEFAULT 0,
                        groundedness_score INT DEFAULT 0,
                        completeness_score INT DEFAULT 0,
                        safety_score INT DEFAULT 0,
                        citation_quality INT DEFAULT 0,
                        evaluator_type TEXT DEFAULT 'heuristic',
                        evaluation_latency_ms DOUBLE PRECISION DEFAULT 0.0,
                        evaluation_reason TEXT DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )

    def save_message(
        self,
        user_id: str | None,
        thread_id: str,
        run_id: str,
        role: str,
        content: str,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        clean_content = (content or "").strip()
        if not clean_content:
            return
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_store (namespace, user_id, thread_id, run_id, role, content, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        self.namespace,
                        clean_user_id,
                        thread_id,
                        run_id,
                        role,
                        clean_content,
                        json.dumps(metadata or {}),
                    ),
                )

    def list_messages(
        self,
        thread_id: str,
        limit: int = 50,
        user_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 200))
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                if clean_user_id:
                    cur.execute(
                        """
                        SELECT user_id, thread_id, run_id, role, content, metadata, created_at
                        FROM conversation_store
                        WHERE namespace = %s AND user_id = %s AND thread_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (self.namespace, clean_user_id, thread_id, bounded_limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT user_id, thread_id, run_id, role, content, metadata, created_at
                        FROM conversation_store
                        WHERE namespace = %s AND thread_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (self.namespace, thread_id, bounded_limit),
                    )
                rows = cur.fetchall()

        output: List[Dict[str, Any]] = []
        for row in rows:
            raw_meta = row[5]
            if isinstance(raw_meta, dict):
                metadata = raw_meta
            elif isinstance(raw_meta, str):
                try:
                    metadata = json.loads(raw_meta)
                except Exception:
                    metadata = {}
            else:
                metadata = {}
            output.append(
                {
                    "user_id": row[0] or "",
                    "thread_id": row[1],
                    "run_id": row[2],
                    "role": row[3],
                    "content": row[4],
                    "metadata": metadata,
                    "created_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
                }
            )
        return output

    def list_threads(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            return []
        bounded_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT thread_id, MAX(created_at) AS last_message_at, COUNT(*) AS message_count
                    FROM conversation_store
                    WHERE namespace = %s AND user_id = %s
                    GROUP BY thread_id
                    ORDER BY MAX(created_at) DESC
                    LIMIT %s
                    """,
                    (self.namespace, clean_user_id, bounded_limit),
                )
                rows = cur.fetchall()

                output: List[Dict[str, Any]] = []
                for thread_id, last_message_at, message_count in rows:
                    cur.execute(
                        """
                        SELECT content
                        FROM conversation_store
                        WHERE namespace = %s AND user_id = %s AND thread_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                        """,
                        (self.namespace, clean_user_id, thread_id),
                    )
                    preview_row = cur.fetchone()
                    output.append(
                        {
                            "thread_id": thread_id,
                            "message_count": int(message_count or 0),
                            "last_message_at": (
                                last_message_at.isoformat()
                                if hasattr(last_message_at, "isoformat")
                                else str(last_message_at)
                            ),
                            "last_message_preview": (preview_row[0] if preview_row else "") or "",
                        }
                    )
        return output

    def create_user(self, user_id: str, password_hash: str) -> bool:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            raise ValueError("user_id is required")
        if not password_hash:
            raise ValueError("password_hash is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (namespace, user_id, password_hash)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (namespace, user_id) DO NOTHING
                    RETURNING user_id
                    """,
                    (self.namespace, clean_user_id, password_hash),
                )
                row = cur.fetchone()
        return row is not None

    def get_user_password_hash(self, user_id: str) -> str | None:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT password_hash
                    FROM users
                    WHERE namespace = %s AND user_id = %s
                    LIMIT 1
                    """,
                    (self.namespace, clean_user_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        return str(row[0] or "")

    def save_hitl_event(
        self,
        user_id: str | None,
        thread_id: str | None,
        query: str,
        decision: str,
        reason: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        clean_query = (query or "").strip()
        clean_decision = (decision or "").strip().lower()
        if not clean_query or clean_decision not in {"approved", "rejected"}:
            return
        clean_user_id = (user_id or "").strip()
        clean_thread_id = (thread_id or "").strip()
        clean_reason = (reason or "").strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hitl_events (namespace, user_id, thread_id, query, decision, reason, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        self.namespace,
                        clean_user_id,
                        clean_thread_id,
                        clean_query,
                        clean_decision,
                        clean_reason,
                        json.dumps(metadata or {}),
                    ),
                )

    def list_hitl_events(
        self,
        user_id: str,
        limit: int = 50,
        thread_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        clean_user_id = (user_id or "").strip()
        if not clean_user_id:
            return []
        clean_thread_id = (thread_id or "").strip()
        bounded_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            with conn.cursor() as cur:
                if clean_thread_id:
                    cur.execute(
                        """
                        SELECT user_id, thread_id, query, decision, reason, metadata, created_at
                        FROM hitl_events
                        WHERE namespace = %s AND user_id = %s AND thread_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (self.namespace, clean_user_id, clean_thread_id, bounded_limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT user_id, thread_id, query, decision, reason, metadata, created_at
                        FROM hitl_events
                        WHERE namespace = %s AND user_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (self.namespace, clean_user_id, bounded_limit),
                    )
                rows = cur.fetchall()

        output: List[Dict[str, Any]] = []
        for row in rows:
            raw_meta = row[5]
            if isinstance(raw_meta, dict):
                metadata = raw_meta
            elif isinstance(raw_meta, str):
                try:
                    metadata = json.loads(raw_meta)
                except Exception:
                    metadata = {}
            else:
                metadata = {}
            output.append(
                {
                    "user_id": row[0] or "",
                    "thread_id": row[1] or "",
                    "query": row[2],
                    "decision": row[3],
                    "reason": row[4] or "",
                    "metadata": metadata,
                    "created_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
                }
            )
        return output

    def save_trace(
        self,
        trace_id: str,
        user_id: str | None,
        thread_id: str | None,
        status: str = "running",
        duration_ms: float = 0.0,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        clean_user_id = (user_id or "").strip()
        clean_thread_id = (thread_id or "").strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO traces (trace_id, namespace, user_id, thread_id, status, duration_ms, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (trace_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        duration_ms = EXCLUDED.duration_ms,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        trace_id,
                        self.namespace,
                        clean_user_id,
                        clean_thread_id,
                        status,
                        duration_ms,
                        json.dumps(metadata or {}),
                    ),
                )

    def save_agent_execution(
        self,
        trace_id: str,
        agent_name: str,
        user_id: str | None,
        thread_id: str | None,
        start_time: str,
        end_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        route_selected: str = "",
        input_metadata: Dict[str, Any] | None = None,
        output_metadata: Dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> None:
        clean_user_id = (user_id or "").strip()
        clean_thread_id = (thread_id or "").strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_executions (
                        namespace, trace_id, agent_name, user_id, thread_id,
                        start_time, end_time, duration_ms, success, error_type,
                        route_selected, input_metadata, output_metadata, retry_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    """,
                    (
                        self.namespace, trace_id, agent_name, clean_user_id, clean_thread_id,
                        start_time, end_time, duration_ms, success, error_type,
                        route_selected, json.dumps(input_metadata or {}), json.dumps(output_metadata or {}), retry_count
                    ),
                )

    def save_llm_call(
        self,
        trace_id: str,
        agent_name: str,
        model_name: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        latency_ms: float,
        estimated_cost: float,
        success: bool,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO llm_calls (
                        namespace, trace_id, agent_name, model_name, provider,
                        input_tokens, output_tokens, total_tokens, latency_ms,
                        estimated_cost, success, error
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.namespace, trace_id, agent_name, model_name, provider,
                        input_tokens, output_tokens, total_tokens, latency_ms,
                        estimated_cost, success, error
                    ),
                )

    def save_retrieval_event(
        self,
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
        evaluation_signals: Dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO retrieval_events (
                        namespace, trace_id, query, num_retrieved_docs,
                        retrieval_latency_ms, reranking_latency_ms, top_k,
                        retrieval_method, vector_count, bm25_count, reranked_count,
                        sources, scores, evaluation_signals
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                    """,
                    (
                        self.namespace, trace_id, query, num_retrieved_docs,
                        retrieval_latency_ms, reranking_latency_ms, top_k,
                        retrieval_method, vector_count, bm25_count, reranked_count,
                        json.dumps(sources or []), json.dumps(scores or []),
                        json.dumps(evaluation_signals or {})
                    ),
                )

    def save_mcp_tool_call(
        self,
        trace_id: str,
        tool_name: str,
        start_time: str,
        duration_ms: float,
        success: bool,
        error_type: str = "",
        input_metadata: Dict[str, Any] | None = None,
        output_metadata: Dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_tool_calls (
                        namespace, trace_id, tool_name, start_time, duration_ms,
                        success, error_type, input_metadata, output_metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        self.namespace, trace_id, tool_name, start_time, duration_ms,
                        success, error_type,
                        json.dumps(input_metadata or {}), json.dumps(output_metadata or {})
                    ),
                )

    def save_evaluation_result(
        self,
        trace_id: str,
        overall_score: int,
        relevance_score: int,
        groundedness_score: int,
        completeness_score: int,
        safety_score: int,
        citation_quality: int,
        evaluator_type: str,
        evaluation_latency_ms: float,
        evaluation_reason: str,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO evaluation_results (
                        namespace, trace_id, overall_score, relevance_score,
                        groundedness_score, completeness_score, safety_score,
                        citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.namespace, trace_id, overall_score, relevance_score,
                        groundedness_score, completeness_score, safety_score,
                        citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason
                    ),
                )

    def get_traces(
        self,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> List[Dict[str, Any]]:
        clean_user_id = (user_id or "").strip()
        bounded_limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        sql = "SELECT trace_id, user_id, thread_id, status, duration_ms, metadata, created_at FROM traces WHERE namespace = %s"
        params: list[Any] = [self.namespace]
        if clean_user_id:
            sql += " AND user_id = %s"
            params.append(clean_user_id)
        if status:
            sql += " AND status = %s"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([bounded_limit, offset])

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        traces = []
        for r in rows:
            meta = r[5] if isinstance(r[5], dict) else {}
            traces.append({
                "trace_id": r[0],
                "user_id": r[1],
                "thread_id": r[2],
                "status": r[3],
                "duration_ms": r[4],
                "metadata": meta,
                "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6])
            })
        return traces

    def get_trace_by_id(
        self,
        trace_id: str,
        user_id: str | None = None,
    ) -> Dict[str, Any] | None:
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                sql = "SELECT trace_id, user_id, thread_id, status, duration_ms, metadata, created_at FROM traces WHERE namespace = %s AND trace_id = %s"
                params: list[Any] = [self.namespace, trace_id]
                if clean_user_id:
                    sql += " AND user_id = %s"
                    params.append(clean_user_id)
                cur.execute(sql, params)
                row = cur.fetchone()
                if not row:
                    return None

                meta = row[5] if isinstance(row[5], dict) else {}

                trace = {
                    "trace_id": row[0],
                    "user_id": row[1],
                    "thread_id": row[2],
                    "status": row[3],
                    "duration_ms": row[4],
                    "metadata": meta,
                    "created_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
                    "agent_executions": [],
                    "llm_calls": [],
                    "retrieval_events": [],
                    "mcp_tool_calls": [],
                    "evaluation_results": []
                }

                cur.execute(
                    "SELECT agent_name, start_time, end_time, duration_ms, success, error_type, route_selected, input_metadata, output_metadata FROM agent_executions WHERE namespace = %s AND trace_id = %s ORDER BY id ASC",
                    (self.namespace, trace_id)
                )
                for er in cur.fetchall():
                    trace["agent_executions"].append({
                        "agent_name": er[0],
                        "start_time": er[1],
                        "end_time": er[2],
                        "duration_ms": er[3],
                        "success": bool(er[4]),
                        "error_type": er[5],
                        "route_selected": er[6],
                        "input_metadata": er[7] if isinstance(er[7], dict) else {},
                        "output_metadata": er[8] if isinstance(er[8], dict) else {},
                    })

                cur.execute(
                    "SELECT agent_name, model_name, provider, input_tokens, output_tokens, total_tokens, latency_ms, estimated_cost, success, error FROM llm_calls WHERE namespace = %s AND trace_id = %s ORDER BY id ASC",
                    (self.namespace, trace_id)
                )
                for lr in cur.fetchall():
                    trace["llm_calls"].append({
                        "agent_name": lr[0],
                        "model_name": lr[1],
                        "provider": lr[2],
                        "input_tokens": lr[3],
                        "output_tokens": lr[4],
                        "total_tokens": lr[5],
                        "latency_ms": lr[6],
                        "estimated_cost": lr[7],
                        "success": bool(lr[8]),
                        "error": lr[9]
                    })

                cur.execute(
                    "SELECT query, num_retrieved_docs, retrieval_latency_ms, reranking_latency_ms, top_k, retrieval_method, vector_count, bm25_count, reranked_count, sources, scores, evaluation_signals FROM retrieval_events WHERE namespace = %s AND trace_id = %s ORDER BY id ASC",
                    (self.namespace, trace_id)
                )
                for rr in cur.fetchall():
                    trace["retrieval_events"].append({
                        "query": rr[0],
                        "num_retrieved_docs": rr[1],
                        "retrieval_latency_ms": rr[2],
                        "reranking_latency_ms": rr[3],
                        "top_k": rr[4],
                        "retrieval_method": rr[5],
                        "vector_count": rr[6],
                        "bm25_count": rr[7],
                        "reranked_count": rr[8],
                        "sources": rr[9] if isinstance(rr[9], list) else [],
                        "scores": rr[10] if isinstance(rr[10], list) else [],
                        "evaluation_signals": rr[11] if isinstance(rr[11], dict) else {}
                    })

                cur.execute(
                    "SELECT tool_name, start_time, duration_ms, success, error_type, input_metadata, output_metadata FROM mcp_tool_calls WHERE namespace = %s AND trace_id = %s ORDER BY id ASC",
                    (self.namespace, trace_id)
                )
                for mr in cur.fetchall():
                    trace["mcp_tool_calls"].append({
                        "tool_name": mr[0],
                        "start_time": mr[1],
                        "duration_ms": mr[2],
                        "success": bool(mr[3]),
                        "error_type": mr[4],
                        "input_metadata": mr[5] if isinstance(mr[5], dict) else {},
                        "output_metadata": mr[6] if isinstance(mr[6], dict) else {},
                    })

                cur.execute(
                    "SELECT overall_score, relevance_score, groundedness_score, completeness_score, safety_score, citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason FROM evaluation_results WHERE namespace = %s AND trace_id = %s ORDER BY id ASC",
                    (self.namespace, trace_id)
                )
                for ev in cur.fetchall():
                    trace["evaluation_results"].append({
                        "overall_score": ev[0],
                        "relevance_score": ev[1],
                        "groundedness_score": ev[2],
                        "completeness_score": ev[3],
                        "safety_score": ev[4],
                        "citation_quality": ev[5],
                        "evaluator_type": ev[6],
                        "evaluation_latency_ms": ev[7],
                        "evaluation_reason": ev[8]
                    })

            return trace

    def get_observability_metrics(self, user_id: str | None = None) -> Dict[str, Any]:
        clean_user_id = (user_id or "").strip()
        with self._connect() as conn:
            with conn.cursor() as cur:
                where_clause = "WHERE namespace = %s"
                params: list[Any] = [self.namespace]
                if clean_user_id:
                    where_clause += " AND user_id = %s"
                    params.append(clean_user_id)

                cur.execute(
                    f"SELECT COUNT(*) as total_requests, SUM(CASE WHEN status != 'failed' THEN 1 ELSE 0 END) as success_count, AVG(duration_ms) as avg_latency FROM traces {where_clause}",
                    params
                )
                t_row = cur.fetchone()
                total_requests = (t_row[0] if t_row else 0) or 0
                success_count = (t_row[1] if t_row else 0) or 0
                avg_latency = float((t_row[2] if t_row else 0.0) or 0.0)
                success_rate = (success_count / total_requests) if total_requests > 0 else 1.0
                error_rate = 1.0 - success_rate

                cur.execute(
                    "SELECT COUNT(*) as total_calls, SUM(total_tokens) as total_tokens, SUM(estimated_cost) as total_cost, AVG(latency_ms) as avg_llm_latency FROM llm_calls WHERE namespace = %s",
                    [self.namespace]
                )
                llm_row = cur.fetchone()
                total_llm_calls = (llm_row[0] if llm_row else 0) or 0
                total_tokens = (llm_row[1] if llm_row else 0) or 0
                estimated_cost = float((llm_row[2] if llm_row else 0.0) or 0.0)
                avg_llm_latency = float((llm_row[3] if llm_row else 0.0) or 0.0)

                cur.execute(
                    "SELECT AVG(overall_score) as avg_score, SUM(CASE WHEN overall_score < 60 THEN 1 ELSE 0 END) as low_quality_count FROM evaluation_results WHERE namespace = %s",
                    [self.namespace]
                )
                eval_row = cur.fetchone()
                avg_eval_score = float((eval_row[0] if eval_row else 0.0) or 0.0)
                low_quality_count = (eval_row[1] if eval_row else 0) or 0

                return {
                    "total_requests": total_requests,
                    "success_rate": round(success_rate, 4),
                    "error_rate": round(error_rate, 4),
                    "avg_latency_ms": round(avg_latency, 2),
                    "p95_latency_ms": round(avg_latency * 1.3, 2),
                    "total_llm_calls": total_llm_calls,
                    "total_tokens": total_tokens,
                    "estimated_cost_usd": round(estimated_cost, 6),
                    "avg_llm_latency_ms": round(avg_llm_latency, 2),
                    "avg_evaluation_score": round(avg_eval_score, 2),
                    "low_quality_count": low_quality_count
                }

    def get_agent_metrics(self, user_id: str | None = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT agent_name, COUNT(*) as count, AVG(duration_ms) as avg_duration_ms, SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as failures FROM agent_executions WHERE namespace = %s GROUP BY agent_name ORDER BY count DESC",
                    [self.namespace]
                )
                rows = cur.fetchall()
                metrics = []
                for r in rows:
                    cnt = r[1] or 0
                    fails = r[3] or 0
                    metrics.append({
                        "agent_name": r[0],
                        "executions": cnt,
                        "avg_duration_ms": round(float(r[2] or 0.0), 2),
                        "failures": fails,
                        "failure_rate": round((fails / cnt) if cnt > 0 else 0.0, 4)
                    })
                return metrics

    def get_tool_metrics(self, user_id: str | None = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tool_name, COUNT(*) as count, AVG(duration_ms) as avg_duration_ms, SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as failures FROM mcp_tool_calls WHERE namespace = %s GROUP BY tool_name ORDER BY count DESC",
                    [self.namespace]
                )
                rows = cur.fetchall()
                metrics = []
                for r in rows:
                    cnt = r[1] or 0
                    fails = r[3] or 0
                    metrics.append({
                        "tool_name": r[0],
                        "calls": cnt,
                        "avg_duration_ms": round(float(r[2] or 0.0), 2),
                        "failures": fails,
                        "failure_rate": round((fails / cnt) if cnt > 0 else 0.0, 4)
                    })
                return metrics

    def get_evaluations(
        self,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trace_id, overall_score, relevance_score, groundedness_score, completeness_score, safety_score, citation_quality, evaluator_type, evaluation_latency_ms, evaluation_reason, created_at FROM evaluation_results WHERE namespace = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    [self.namespace, bounded_limit, offset]
                )
                rows = cur.fetchall()
                evals = []
                for r in rows:
                    evals.append({
                        "trace_id": r[0],
                        "overall_score": r[1],
                        "relevance_score": r[2],
                        "groundedness_score": r[3],
                        "completeness_score": r[4],
                        "safety_score": r[5],
                        "citation_quality": r[6],
                        "evaluator_type": r[7],
                        "evaluation_latency_ms": r[8],
                        "evaluation_reason": r[9],
                        "created_at": r[10].isoformat() if hasattr(r[10], "isoformat") else str(r[10])
                    })
                return evals



def open_conversation_store(
    postgres_uri: str,
    sqlite_path: str,
    namespace: str = "default",
    fallback_sqlite: bool = True,
) -> StoreOpenResult:
    if postgres_uri:
        try:
            store = PostgresConversationStore(postgres_uri, namespace=namespace)
            store.setup()
            return StoreOpenResult(store=store, backend_label="postgres")
        except Exception as e:
            if not fallback_sqlite:
                raise RuntimeError(f"Failed to open Postgres store: {e}")
            print(f"[service] Failed to open Postgres store: {e}. Falling back to SQLite store.")

    store = SQLiteConversationStore(sqlite_path, namespace=namespace)
    store.setup()
    return StoreOpenResult(store=store, backend_label=sqlite_path)
