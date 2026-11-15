"""Ollama client for LocalTestForge.

Interacts exclusively with the local Ollama daemon at http://127.0.0.1:11434.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

__all__ = ["GenerationResult", "OllamaClient"]


@dataclass(frozen=True)
class GenerationResult:
    """Result of a generation call to Ollama."""

    text: str
    done_reason: str
    latency_s: float
    eval_count: int
    tokens_per_s: float


class OllamaClient:
    """Client for local Ollama server at http://127.0.0.1:11434."""

    def __init__(
        self,
        model: str = "qwen2.5-coder:1.5b",
        base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.digest: str | None = self._lookup_digest()

    def _lookup_digest(self) -> str | None:
        """Look up model digest from /api/tags, returning None if unavailable."""
        url = f"{self.base_url}/api/tags"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                for m in models:
                    name = m.get("name", "")
                    if name == self.model or name.split(":")[0] == self.model:
                        return m.get("digest")
                    if ":" not in self.model and name == f"{self.model}:latest":
                        return m.get("digest")
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, KeyError):
            return None
        return None

    def generate(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Generate test candidate text from prompt.

        Network failures propagate as OSError.
        """
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options or {},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        start_time = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=300.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OSError(f"HTTP error from Ollama: {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"Failed to connect to Ollama: {exc.reason}") from exc

        duration_s = max(0.001, round(time.perf_counter() - start_time, 3))
        text = data.get("response", "")
        done_reason = data.get("done_reason", "stop")
        eval_count = int(data.get("eval_count", 0))
        eval_duration = data.get("eval_duration")

        if eval_duration and eval_duration > 0:
            tokens_per_s = round(eval_count / (eval_duration / 1e9), 1)
        elif eval_count > 0:
            tokens_per_s = round(eval_count / duration_s, 1)
        else:
            tokens_per_s = 0.0

        return GenerationResult(
            text=text,
            done_reason=done_reason,
            latency_s=duration_s,
            eval_count=eval_count,
            tokens_per_s=tokens_per_s,
        )
