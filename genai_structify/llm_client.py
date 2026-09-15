"""
Thin wrapper around a local Ollama server.

Kept as its own module so the rest of the codebase never talks to a
specific model runtime directly -- swapping to a different local model,
or back to a hosted API, means editing exactly one file.

Requires Ollama running locally: https://ollama.com
    ollama pull gemma2:2b
    ollama serve   (usually already running as a background service)
"""

from __future__ import annotations

import json
import os

import requests


class LLMError(RuntimeError):
    """Raised when the LLM call fails or returns something we can't use."""


class LLMClient:
    def __init__(self, model: str = "gemma2:2b", host: str | None = None, timeout: int = 120):
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.timeout = timeout
        self._check_server()

    def _check_server(self) -> None:
        try:
            requests.get(f"{self.host}/api/tags", timeout=5)
        except requests.exceptions.ConnectionError as exc:
            raise LLMError(
                f"Couldn't reach Ollama at {self.host}. Is it running? "
                "Install from https://ollama.com and run `ollama serve`, "
                f"then `ollama pull {self.model}`."
            ) from exc

    def extract_json(self, system_prompt: str, user_content: str, max_tokens: int = 4000):
        """
        Send a prompt that asks the model to return ONLY JSON, and parse it.

        Retries once with a stricter instruction if the first response
        isn't valid JSON -- local models are noticeably more prone to
        wrapping output in prose or markdown fences than hosted frontier
        models, so this retry matters more here than it would with e.g.
        a hosted Claude/GPT model.
        """
        raw = self._call(system_prompt, user_content, max_tokens)
        parsed = self._try_parse(raw)
        if parsed is not None:
            return parsed

        stricter_system = (
            system_prompt
            + "\n\nIMPORTANT: Your previous response could not be parsed as JSON. "
            "Respond with ONLY a raw JSON value. No markdown code fences, no preamble, "
            "no explanation -- just the JSON itself."
        )
        raw_retry = self._call(stricter_system, user_content, max_tokens)
        parsed_retry = self._try_parse(raw_retry)
        if parsed_retry is not None:
            return parsed_retry

        raise LLMError(
            "Model did not return parseable JSON after 2 attempts. "
            f"Last raw output (truncated): {raw_retry[:300]!r}"
        )

    def _call(self, system_prompt: str, user_content: str, max_tokens: int) -> str:
        try:
            response = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "system": system_prompt,
                    "prompt": user_content,
                    "stream": False,
                    "format": "json",
                    "options": {"num_predict": max_tokens},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"Ollama call failed: {exc}") from exc

        return response.json().get("response", "").strip()

    @staticmethod
    def _try_parse(raw: str):
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.removeprefix("json")
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None
