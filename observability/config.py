import os
from dataclasses import dataclass, field
from typing import Dict, Tuple

def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in {"1", "true", "yes", "on"}

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except ValueError:
        return default

# Per-token pricing (input_cost_per_1k, output_cost_per_1k)
DEFAULT_MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    "gpt-4o-mini": (0.00015 / 1000, 0.00060 / 1000),
    "gpt-4o": (0.0025 / 1000, 0.0100 / 1000),
    "llama-3.1-70b": (0.00059 / 1000, 0.00079 / 1000),
    "llama-3.1-8b": (0.00005 / 1000, 0.00008 / 1000),
}

@dataclass
class ObservabilityConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("OBSERVABILITY_ENABLED", True))
    persistence_enabled: bool = field(default_factory=lambda: _env_bool("OBSERVABILITY_PERSISTENCE_ENABLED", True))
    redact_content: bool = field(default_factory=lambda: _env_bool("OBSERVABILITY_REDACT_CONTENT", True))
    retention_days: int = field(default_factory=lambda: _env_int("OBSERVABILITY_RETENTION_DAYS", 30))
    llm_evaluation_enabled: bool = field(default_factory=lambda: _env_bool("LLM_EVALUATION_ENABLED", False))
    model_pricing: Dict[str, Tuple[float, float]] = field(default_factory=lambda: DEFAULT_MODEL_PRICING)

    @classmethod
    def from_env(cls) -> "ObservabilityConfig":
        return cls()

config = ObservabilityConfig.from_env()
