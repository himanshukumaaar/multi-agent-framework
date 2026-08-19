from contextvars import ContextVar
from typing import Optional
from uuid import uuid4

_trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)
_user_id_ctx: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
_thread_id_ctx: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)


def get_trace_id() -> str:
    """Retrieve active trace_id or generate a fallback standard UUID."""
    tid = _trace_id_ctx.get()
    if not tid:
        tid = str(uuid4())
        _trace_id_ctx.set(tid)
    return tid


def set_trace_id(trace_id: Optional[str]) -> str:
    """Set active trace_id."""
    clean_id = (trace_id or "").strip() or str(uuid4())
    _trace_id_ctx.set(clean_id)
    return clean_id


def get_user_id() -> Optional[str]:
    return _user_id_ctx.get()


def set_user_id(user_id: Optional[str]) -> None:
    _user_id_ctx.set((user_id or "").strip() or None)


def get_thread_id() -> Optional[str]:
    return _thread_id_ctx.get()


def set_thread_id(thread_id: Optional[str]) -> None:
    _thread_id_ctx.set((thread_id or "").strip() or None)


def clear_context() -> None:
    _trace_id_ctx.set(None)
    _user_id_ctx.set(None)
    _thread_id_ctx.set(None)
