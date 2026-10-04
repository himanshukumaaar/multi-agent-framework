import asyncio, base64, hashlib, hmac, inspect, json, os, re, sqlite3, time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, Tuple
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.runnables import RunnableConfig
from langsmith import Client as LangsmithClient
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest

try:
    from langgraph.checkpoint.aiosqlite import AsyncSqliteSaver
except Exception:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

try:
    from langgraph.graph import CompiledGraph
except Exception:
    from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from agent import research_assistant
from agent.tools import perform_web_search
from schema import (
    AuthLoginInput, AuthRegisterInput, AuthToken, ChatMessage,
    Feedback, StreamInput, UserInput, model_dump_compat,
)
from service.persistence_store import open_conversation_store
from observability import (
    set_persistence_store, set_trace_id, get_trace_id,
    set_user_id, set_thread_id,
)

load_dotenv()

# Configuration
CHECKPOINT_DB_PATH = os.getenv("CHECKPOINT_DB_PATH", "checkpoints.db")
POSTGRES_CHECKPOINT_URI = os.getenv("POSTGRES_CHECKPOINT_URI") or os.getenv("DATABASE_URL", "")
CHECKPOINT_FALLBACK_SQLITE = os.getenv("CHECKPOINT_FALLBACK_SQLITE", "true").lower() not in {"0", "false", "no", "off"}
CHECKPOINT_NAMESPACE = os.getenv("CHECKPOINT_NAMESPACE", "default")
POSTGRES_STORE_URI = os.getenv("POSTGRES_STORE_URI") or POSTGRES_CHECKPOINT_URI
STORE_DB_PATH = os.getenv("STORE_DB_PATH", "store.db")
STORE_FALLBACK_SQLITE = os.getenv("STORE_FALLBACK_SQLITE", "true").lower() not in {"0", "false", "no", "off"}
STORE_NAMESPACE = os.getenv("STORE_NAMESPACE", "default")
ENABLE_USER_AUTH = os.getenv("ENABLE_USER_AUTH", "true").lower() not in {"0", "false", "no", "off"}
USER_AUTH_SECRET = os.getenv("USER_AUTH_SECRET") or os.getenv("AUTH_SECRET") or "dev-insecure-user-auth-secret-change-me"
USER_AUTH_TOKEN_TTL_SECONDS = int(os.getenv("USER_AUTH_TOKEN_TTL_SECONDS", "86400"))
PASSWORD_HASH_ITERATIONS = max(100000, int(os.getenv("PASSWORD_HASH_ITERATIONS", "210000")))
_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")

# Metrics
HTTP_REQUESTS_TOTAL = Counter("http_requests_total", "Total number of HTTP requests", ["method", "path", "status_code"])
HTTP_REQUEST_DURATION_SECONDS = Histogram("http_request_duration_seconds", "HTTP request duration in seconds", ["method", "path"])

# --- Helper & Auth Utilities ---
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def _b64url_decode(val: str) -> bytes:
    return base64.urlsafe_b64decode(val + "=" * ((4 - len(val) % 4) % 4))

def _sign(data: str) -> str:
    return _b64url_encode(hmac.new(USER_AUTH_SECRET.encode(), data.encode(), hashlib.sha256).digest())

def _create_access_token(user_id: str) -> str:
    now = int(time.time())
    payload = json.dumps({"sub": user_id, "iat": now, "exp": now + max(60, USER_AUTH_TOKEN_TTL_SECONDS)}, separators=(",", ":")).encode()
    segment = _b64url_encode(payload)
    return f"{segment}.{_sign(segment)}"

def _verify_access_token(token: str) -> str | None:
    try:
        segment, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(segment)): return None
        payload = json.loads(_b64url_decode(segment).decode())
        if not payload.get("sub") or int(payload.get("exp", 0)) <= time.time(): return None
        return str(payload["sub"]).strip()
    except Exception:
        return None

def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_HASH_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${_b64url_encode(salt)}${_b64url_encode(digest)}"

def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iters, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256": return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _b64url_decode(salt_b64), int(iters))
        return hmac.compare_digest(actual, _b64url_decode(digest_b64))
    except Exception:
        return False

def _validate_credentials(user_id: str, password: str) -> Tuple[str, str]:
    u, p = (user_id or "").strip(), (password or "").strip()
    if not _USER_ID_PATTERN.match(u): raise HTTPException(400, "Invalid user_id (3-64 alphanumeric/dot/dash/underscore chars).")
    if len(p) < 8: raise HTTPException(400, "Password must be at least 8 characters.")
    return u, p

def _get_request_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", "")
    if not user_id: raise HTTPException(401, "Authentication required.")
    return str(user_id)

def _rotate_incompatible_checkpoint_db(db_path: str) -> str:
    db_file = Path(db_path)
    if not db_file.exists(): return db_path
    try:
        with sqlite3.connect(str(db_file)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints' LIMIT 1").fetchone():
                return db_path
            cols = {row[1] for row in conn.execute("PRAGMA table_info(checkpoints)").fetchall()}
            if "thread_ts" in cols: return db_path
    except sqlite3.DatabaseError:
        pass
    stamp = int(time.time())
    backup = db_file.with_name(f"{db_file.name}.legacy-{stamp}")
    try:
        os.replace(str(db_file), str(backup))
        return db_path
    except PermissionError:
        return str(db_file.with_name(f"{db_file.stem}.runtime-{stamp}{db_file.suffix}"))

async def _open_checkpointer(stack: AsyncExitStack):
    Path(CHECKPOINT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    resolved_db = _rotate_incompatible_checkpoint_db(CHECKPOINT_DB_PATH)
    saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(resolved_db))
    for m in ("setup", "asetup"):
        method = getattr(saver, m, None)
        if callable(method):
            res = method()
            if inspect.isawaitable(res): await res
            break
    return saver, resolved_db

class TokenQueueStreamingHandler(AsyncCallbackHandler):
    def __init__(self, queue: asyncio.Queue): self.queue = queue
    async def on_llm_new_token(self, token: str, **kwargs) -> None:
        if token: await self.queue.put(token)

# --- HITL & Web Parsing Utilities ---
def _parse_web_preview_items(notes: str, recency_days: int) -> list[dict[str, Any]]:
    notes = (notes or "").strip()
    if not notes or notes.lower().startswith("web retrieval failed:"): return []
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, recency_days)) if recency_days > 0 else None
    for block in [b.strip() for b in re.split(r"\n(?=-\s)", notes) if b.strip()]:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines or not lines[0].startswith("- "): continue
        title, url, date_text, snippet_parts = lines[0][2:].strip(), "", "", []
        for line in lines[1:]:
            l = line.lower()
            if l.startswith("link:"): url = line.split(":", 1)[1].strip()
            elif l.startswith("date:"): date_text = line.split(":", 1)[1].strip()
            elif l.startswith("snippet:"): snippet_parts.append(line.split(":", 1)[1].strip())
            else: snippet_parts.append(line)
        pub_dt = None
        try: pub_dt = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception: pass
        items.append({
            "title": title, "url": url, "snippet": " ".join(snippet_parts),
            "published_date": date_text, "is_within_recency": pub_dt >= cutoff if (cutoff and pub_dt) else None
        })
    return items or [{"title": notes.splitlines()[0][:120], "url": "", "snippet": notes[:600], "published_date": "", "is_within_recency": None}]

def _maybe_rewrite_hitl_message(app: FastAPI, user_id: str | None, thread_id: str, raw_msg: str) -> str:
    msg = (raw_msg or "").strip()
    if not msg or "WEB_HITL_DECISION?" in msg or "__WEB_HITL__|" in msg: return msg
    action, reason = "", ""
    l = msg.lower()
    if l.startswith("approve") or l in {"yes", "y", "ok", "okay", "continue", "proceed"}: action = "approve"
    elif l.startswith("reject"): action, reason = "reject", msg[6:].strip(" :-")
    elif l.startswith("no"): action, reason = "reject", msg[2:].strip(" :-")
    if not action: return msg

    cache = getattr(app.state, "web_hitl_pending_cache", {})
    pending = cache.get(f"{(user_id or '').strip()}::{thread_id}") or {}
    query = str(pending.get("query") or "").strip()
    if not query: return msg
    return f"WEB_HITL_DECISION?action={action}&route={pending.get('route', 'web')}&days={max(0, int(pending.get('recency_days', 0)))}&query_b64={_b64url_encode(query.encode())}&reason_b64={_b64url_encode(reason.encode())}"

def _update_hitl_cache_and_audit(app: FastAPI, user_id: str | None, thread_id: str, state: Dict[str, Any]):
    cache = getattr(app.state, "web_hitl_pending_cache", None)
    cache_key = f"{(user_id or '').strip()}::{thread_id}"
    decision = str(state.get("web_hitl_decision") or "").strip().lower()
    if decision in {"approved", "rejected"} and isinstance(cache, dict):
        cache.pop(cache_key, None)
    elif decision == "awaiting" and state.get("web_hitl_pending_query") and isinstance(cache, dict):
        cache[cache_key] = {
            "query": str(state["web_hitl_pending_query"]).strip(),
            "route": str(state.get("web_hitl_pending_route") or state.get("route") or "web").strip().lower(),
            "recency_days": int(state.get("web_hitl_pending_recency_days") or state.get("recency_days") or 0)
        }

    if decision in {"approved", "rejected"}:
        q = str(state.get("web_hitl_audit_query") or state.get("web_hitl_pending_query") or state.get("query") or "").strip()
        if q:
            _store_safely(app, "save_hitl_event", user_id, thread_id, q, decision, "" if decision == "approved" else str(state.get("web_hitl_reject_reason") or "").strip(), {
                "recency_days": int(state.get("web_hitl_pending_recency_days") or 0),
                "preview_count": int(state.get("web_hitl_preview_count") or 0),
                "all_within_recency": bool(state.get("web_hitl_all_within_recency")),
                "source": str(state.get("web_hitl_pending_source_meta") or ""), "cache_hit": False, "audit_source": "graph"
            })

def _store_safely(app: FastAPI, method_name: str, *args, **kwargs):
    store = getattr(app.state, "store", None)
    if store and hasattr(store, method_name):
        try: asyncio.to_thread(getattr(store, method_name), *args, **kwargs)
        except Exception as e: print(f"[service] store {method_name} failed: {e}")

# --- Application Lifespan & Middlewares ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        saver, backend = await _open_checkpointer(stack)
        store_res = open_conversation_store(POSTGRES_STORE_URI, STORE_DB_PATH, STORE_NAMESPACE, STORE_FALLBACK_SQLITE)
        research_assistant.checkpointer = saver
        app.state.agent = research_assistant
        app.state.store, app.state.store_backend = store_res.store, store_res.backend_label
        app.state.web_hitl_pending_cache = {}
        set_persistence_store(store_res.store)
        yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    trace_id = set_trace_id(request.headers.get("X-Trace-ID") or request.headers.get("x-trace-id"))
    request.state.trace_id = trace_id
    start = time.perf_counter()
    path = getattr(request.scope.get("route"), "path", request.url.path)
    try:
        response = await call_next(request)
        status = str(response.status_code)
    except Exception:
        status = "500"
        raise
    finally:
        dur = time.perf_counter() - start
        HTTP_REQUESTS_TOTAL.labels(method=request.method, path=path, status_code=status).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, path=path).observe(dur)
    response.headers["X-Trace-ID"] = trace_id
    return response

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    pub_paths = {"/auth/register", "/auth/login", "/metrics", "/healthz", "/readyz", "/openapi.json", "/docs", "/redoc"}
    if request.url.path in pub_paths or request.url.path.startswith("/docs/"):
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return Response(status_code=401, content="Missing or invalid token")
    token = auth[7:].strip()

    if ENABLE_USER_AUTH:
        user_id = _verify_access_token(token)
        if not user_id: return Response(status_code=401, content="Invalid or expired token")
        request.state.user_id = user_id
    else:
        auth_sec = os.getenv("AUTH_SECRET")
        if auth_sec and token != auth_sec:
            return Response(status_code=401, content="Invalid token")
    return await call_next(request)

# --- Endpoints ---
@app.get("/healthz")
async def healthz(): return {"status": "ok"}

@app.get("/readyz")
async def readyz():
    if not app.state.agent or not app.state.store: raise HTTPException(503, "Service not ready")
    return {"status": "ready"}

@app.get("/metrics")
async def metrics(): return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/auth/register")
async def register(inp: AuthRegisterInput) -> AuthToken:
    u, p = _validate_credentials(inp.user_id, inp.password)
    if not await asyncio.to_thread(app.state.store.create_user, u, _hash_password(p)):
        raise HTTPException(409, "User already exists.")
    return AuthToken(access_token=_create_access_token(u), token_type="bearer", user_id=u, expires_in=USER_AUTH_TOKEN_TTL_SECONDS)

@app.post("/auth/login")
async def login(inp: AuthLoginInput) -> AuthToken:
    u, p = _validate_credentials(inp.user_id, inp.password)
    h = await asyncio.to_thread(app.state.store.get_user_password_hash, u)
    if not h or not _verify_password(p, h):
        raise HTTPException(401, "Invalid user_id or password.")
    return AuthToken(access_token=_create_access_token(u), token_type="bearer", user_id=u, expires_in=USER_AUTH_TOKEN_TTL_SECONDS)

def _parse_input(inp: UserInput | StreamInput, user_id: str | None) -> Tuple[Dict[str, Any], str]:
    run_id, thread_id = uuid4(), inp.thread_id or str(uuid4())
    trace_id = set_trace_id(inp.trace_id or get_trace_id())
    set_user_id(user_id); set_thread_id(thread_id)
    _store_safely(app, "save_trace", trace_id=trace_id, user_id=user_id, thread_id=thread_id, status="running")
    clean_u = (user_id or "").strip()
    return {
        "input": {"messages": [ChatMessage(type="human", content=inp.message).to_langchain()], "trace_id": trace_id},
        "config": RunnableConfig(
            configurable={
                "thread_id": thread_id, "trace_id": trace_id, "checkpoint_id": str(run_id),
                "checkpoint_ns": f"{CHECKPOINT_NAMESPACE}:{clean_u}" if clean_u else CHECKPOINT_NAMESPACE,
                "model": inp.model, "user_id": clean_u,
            },
            run_id=run_id
        )
    }, str(run_id)

@app.post("/invoke")
async def invoke(inp: UserInput, request: Request) -> ChatMessage:
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    eff_inp = UserInput(message=_maybe_rewrite_hitl_message(app, u, inp.thread_id or "", inp.message), model=inp.model, thread_id=inp.thread_id)
    kwargs, run_id = _parse_input(eff_inp, u)
    tid = kwargs["config"]["configurable"]["thread_id"]
    _store_safely(app, "save_message", u, tid, run_id, "human", inp.message, {"model": inp.model})

    try:
        resp = await app.state.agent.ainvoke(**kwargs)
    except Exception as e:
        if "SerializerCompat" in str(e) or "new_versions" in str(e):
            app.state.agent.checkpointer = None
            resp = await app.state.agent.ainvoke(**kwargs)
        else: raise HTTPException(500, str(e))

    out = ChatMessage.from_langchain(resp["messages"][-1])
    out.run_id = run_id
    _store_safely(app, "save_message", u, tid, run_id, "ai", out.content, {"route": resp.get("route"), "evaluation_score": resp.get("evaluation_score")})
    _update_hitl_cache_and_audit(app, u, tid, resp)
    return out

async def message_generator(inp: StreamInput, user_id: str | None) -> AsyncGenerator[str, None]:
    eff_inp = StreamInput(message=_maybe_rewrite_hitl_message(app, user_id, inp.thread_id or "", inp.message), model=inp.model, thread_id=inp.thread_id, stream_tokens=inp.stream_tokens)
    kwargs, run_id = _parse_input(eff_inp, user_id)
    tid = kwargs["config"]["configurable"]["thread_id"]
    _store_safely(app, "save_message", user_id, tid, run_id, "human", inp.message, {"model": inp.model, "stream": True})

    queue = asyncio.Queue(maxsize=10)
    if inp.stream_tokens: kwargs["config"]["callbacks"] = [TokenQueueStreamingHandler(queue)]

    async def run_stream():
        try:
            async for s in app.state.agent.astream(**kwargs, stream_mode="updates"):
                await queue.put(s)
        except Exception as e:
            await queue.put({"__error__": str(e)})
        finally: await queue.put(None)

    task = asyncio.create_task(run_stream())
    seen = set()

    while s := await queue.get():
        if isinstance(s, str):
            yield f"data: {json.dumps({'type': 'token', 'content': s})}\n\n"
        elif isinstance(s, dict) and "__error__" in s:
            yield f"data: {json.dumps({'type': 'error', 'content': s['__error__']})}\n\n"
        elif isinstance(s, dict):
            for _, state in s.items():
                _update_hitl_cache_and_audit(app, user_id, tid, state)
                for msg in state.get("messages", []):
                    c = ChatMessage.from_langchain(msg)
                    c.run_id = run_id
                    if c.type == "human" and c.content == inp.message: continue
                    fp = (c.type, c.content.strip(), c.tool_call_id or "")
                    if c.type == "ai" and fp not in seen:
                        seen.add(fp)
                        _store_safely(app, "save_message", user_id, tid, run_id, "ai", c.content, {"stream": True})
                    yield f"data: {json.dumps({'type': 'message', 'content': model_dump_compat(c)})}\n\n"
    await task
    yield "data: [DONE]\n\n"

@app.post("/stream")
async def stream_agent(inp: StreamInput, request: Request):
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    return StreamingResponse(message_generator(inp, u), media_type="text/event-stream")

@app.post("/web_search/preview")
async def web_search_preview(payload: Dict[str, Any], request: Request):
    query = str(payload.get("query") or payload.get("message") or "").strip()
    if not query: raise HTTPException(400, "query is required")
    days = max(1, min(int(payload.get("recency_days") or 7), 30))
    max_res = max(1, min(int(payload.get("max_results") or 5), 10))

    res = await asyncio.to_thread(perform_web_search, query, max_res, days, query, True)
    notes, meta = res if isinstance(res, tuple) else (str(res), {})
    items = _parse_web_preview_items(notes, days)
    dated = [i for i in items if i.get("published_date")]

    return {
        "query": query, "recency_days": days, "max_results": max_res, "count": len(items),
        "all_within_recency": bool(dated) and all(i.get("is_within_recency") for i in dated),
        "source": str(meta.get("source", "")), "cache_hit": bool(meta.get("cache_hit")),
        "items": items, "web_notes": notes
    }

@app.post("/hitl/web_decision")
async def record_web_hitl_decision(payload: Dict[str, Any], request: Request):
    u = _get_request_user_id(request)
    q, dec = str(payload.get("query") or "").strip(), str(payload.get("decision") or "").lower()
    if not q: raise HTTPException(400, "query is required")
    if dec not in {"approved", "rejected"}: raise HTTPException(400, "decision must be approved or rejected")

    _store_safely(app, "save_hitl_event", u, payload.get("thread_id"), q, dec, "" if dec == "approved" else str(payload.get("reason", "")), payload)
    return {"status": "recorded", "user_id": u, "query": q, "decision": dec}

@app.get("/hitl/web_decisions")
async def list_web_hitl_decisions(request: Request, limit: int = 50, thread_id: str | None = None):
    u = _get_request_user_id(request)
    evts = await asyncio.to_thread(app.state.store.list_hitl_events, u, max(1, min(limit, 200)), thread_id) if app.state.store else []
    return {"user_id": u, "backend": app.state.store_backend, "count": len(evts), "events": evts}

@app.get("/store/threads")
async def list_user_threads(request: Request, limit: int = 30):
    u = _get_request_user_id(request)
    threads = await asyncio.to_thread(app.state.store.list_threads, u, limit) if app.state.store else []
    return {"user_id": u, "backend": app.state.store_backend, "count": len(threads), "threads": threads}

@app.get("/store/{thread_id}")
async def get_thread_store(thread_id: str, request: Request, limit: int = 50):
    u = _get_request_user_id(request)
    msgs = await asyncio.to_thread(app.state.store.list_messages, thread_id, limit, u) if app.state.store else []
    return {"thread_id": thread_id, "user_id": u, "backend": app.state.store_backend, "count": len(msgs), "messages": msgs}

@app.post("/feedback")
async def feedback(fb: Feedback):
    LangsmithClient().create_feedback(run_id=fb.run_id, key=fb.key, score=fb.score, **(fb.kwargs or {}))
    return {"status": "success"}

# --- Observability Endpoints ---
@app.get("/observability/traces")
async def list_traces(request: Request, limit: int = 50, offset: int = 0, status: str | None = None):
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    t = await asyncio.to_thread(app.state.store.get_traces, u, limit, offset, status) if app.state.store else []
    return {"count": len(t), "traces": t}

@app.get("/observability/traces/{trace_id}")
async def get_trace_detail(trace_id: str, request: Request):
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    if not app.state.store: raise HTTPException(404, "Store not available")
    t = await asyncio.to_thread(app.state.store.get_trace_by_id, trace_id, u)
    if not t: raise HTTPException(404, f"Trace {trace_id} not found")
    return t

@app.get("/observability/metrics")
async def get_obs_metrics(request: Request):
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    return await asyncio.to_thread(app.state.store.get_observability_metrics, u) if app.state.store else {}

@app.get("/observability/evaluations")
async def get_obs_evals(request: Request, limit: int = 50, offset: int = 0):
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    evs = await asyncio.to_thread(app.state.store.get_evaluations, u, limit, offset) if app.state.store else []
    return {"count": len(evs), "evaluations": evs}

@app.get("/observability/agents")
async def get_obs_agents(request: Request):
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    return {"agents": await asyncio.to_thread(app.state.store.get_agent_metrics, u) if app.state.store else []}

@app.get("/observability/tools")
async def get_obs_tools(request: Request):
    u = _get_request_user_id(request) if ENABLE_USER_AUTH else None
    return {"tools": await asyncio.to_thread(app.state.store.get_tool_metrics, u) if app.state.store else []}