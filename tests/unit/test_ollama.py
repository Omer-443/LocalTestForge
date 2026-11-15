"""Unit tests for OllamaClient with mocked urllib (no real network)."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from localtestforge.ollama import GenerationResult, OllamaClient


def test_lookup_digest_success() -> None:
    tags_response = {
        "models": [
            {"name": "qwen2.5-coder:1.5b", "digest": "sha256:1234567890abcdef"},
            {"name": "llama3:latest", "digest": "sha256:fedcba0987654321"},
        ]
    }
    mock_resp = io.BytesIO(json.dumps(tags_response).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        client = OllamaClient(model="qwen2.5-coder:1.5b")
        assert client.digest == "sha256:1234567890abcdef"


def test_lookup_digest_unavailable_returns_none() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        client = OllamaClient(model="qwen2.5-coder:1.5b")
        assert client.digest is None


def test_generate_success() -> None:
    tags_resp = io.BytesIO(json.dumps({"models": []}).encode("utf-8"))
    gen_data = {
        "response": "def test_ok(): pass",
        "done_reason": "stop",
        "eval_count": 15,
        "eval_duration": 100_000_000,  # 0.1s -> 150 tok/s
    }
    gen_resp = io.BytesIO(json.dumps(gen_data).encode("utf-8"))

    with patch("urllib.request.urlopen", side_effect=[tags_resp, gen_resp]) as mock_urlopen:
        client = OllamaClient(model="qwen2.5-coder:1.5b")
        result = client.generate("prompt", options={"num_predict": 100})
        assert isinstance(result, GenerationResult)
        assert result.text == "def test_ok(): pass"
        assert result.done_reason == "stop"
        assert result.eval_count == 15
        assert result.tokens_per_s == 150.0

        # Verify request parameters
        call_args = mock_urlopen.call_args_list[1]
        req = call_args[0][0]
        assert req.get_full_url() == "http://127.0.0.1:11434/api/generate"
        payload = json.loads(req.data.decode("utf-8"))
        assert payload["model"] == "qwen2.5-coder:1.5b"
        assert payload["prompt"] == "prompt"
        assert payload["stream"] is False
        assert payload["options"]["num_predict"] == 100


def test_generate_network_failure_propagates_as_oserror() -> None:
    tags_resp = io.BytesIO(json.dumps({"models": []}).encode("utf-8"))
    with patch(
        "urllib.request.urlopen",
        side_effect=[tags_resp, urllib.error.URLError("Connection refused")],
    ):
        client = OllamaClient(model="qwen2.5-coder:1.5b")
        with pytest.raises(OSError):
            client.generate("prompt")
