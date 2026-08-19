import re
from typing import Any, Dict, List, Union
from observability.config import config

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|bearer\s+[a-zA-Z0-9._\-]+|token|auth)", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"gsk_[a-zA-Z0-9]{20,}", re.IGNORECASE),
]

SENSITIVE_KEYS = {
    "password", "secret", "api_key", "apikey", "authorization",
    "access_token", "token", "auth_token", "bearer", "credentials"
}


def sanitize_value(key: str, val: Any) -> Any:
    """Scrub raw secrets and optionally redact content."""
    if val is None:
        return None

    key_lower = str(key).lower()
    if any(s_key in key_lower for s_key in SENSITIVE_KEYS):
        return "[REDACTED_SECRET]"

    if isinstance(val, str):
        # Scrub explicit secret regex patterns
        for pattern in SECRET_PATTERNS:
            if pattern.search(val):
                val = pattern.sub("[REDACTED_SECRET]", val)
        if config.redact_content and key_lower in {"prompt", "content", "query", "response", "message", "notes"}:
            if len(val) > 200:
                return f"{val[:100]}... [REDACTED_LENGTH_{len(val)}]"
        return val

    if isinstance(val, dict):
        return sanitize_dict(val)

    if isinstance(val, list):
        return [sanitize_value(key, item) for item in val[:20]]

    return val


def sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize dictionary metadata."""
    if not isinstance(data, dict):
        return {}
    sanitized = {}
    for k, v in data.items():
        sanitized[k] = sanitize_value(k, v)
    return sanitized


def scrub_secrets(text: str) -> str:
    """Scrub secret patterns from text."""
    if not isinstance(text, str):
        return text
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_API_KEY]", text)
    return text


def redact_content(data: Dict[str, Any]) -> Dict[str, Any]:
    """Alias for sanitize_dict."""
    return sanitize_dict(data)
