"""
Multi-Provider LLM Client
===========================
Thin wrapper over 10+ LLM provider APIs.
Auto-selects provider based on available API keys.

Usage:
    from contextsynapse.llm import get_llm_client

    llm = get_llm_client()                              # auto-detect
    llm = get_llm_client(provider="groq")               # Groq
    llm = get_llm_client(provider="gemini")              # Google Gemini
    llm = get_llm_client(provider="deepseek")            # DeepSeek
    llm = get_llm_client(provider="together")            # Together AI
    llm = get_llm_client(provider="mistral")             # Mistral
    llm = get_llm_client(provider="cerebras")            # Cerebras
    llm = get_llm_client(provider="fireworks")           # Fireworks AI
    llm = get_llm_client(provider="perplexity")          # Perplexity
    llm = get_llm_client(provider="cohere")              # Cohere
    llm = get_llm_client(provider="openai")              # OpenAI
    llm = get_llm_client(provider="anthropic")           # Anthropic
    llm = get_llm_client(provider="ollama")              # Ollama (local)

    text = llm.generate("Summarize this.", system="You are a summarizer.")
    data = llm.generate_json("Extract entities.", system="Return JSON.")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_cached_client = None

# Providers that use the OpenAI-compatible API (base_url differs)
_OPENAI_COMPATIBLE = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "default_model": "llama-3.1-8b-instant",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "key_env": "TOGETHER_API_KEY",
        "default_model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
        "default_model": "mistral-large-latest",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "default_model": "llama3.1-70b",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "key_env": "FIREWORKS_API_KEY",
        "default_model": "accounts/fireworks/models/llama-v3p1-70b-instruct",
    },
    "perplexity": {
        "base_url": "https://api.perplexity.ai",
        "key_env": "PERPLEXITY_API_KEY",
        "default_model": "sonar-pro",
    },
}


class LLMClient:
    """Multi-provider LLM client with structured output support."""

    def __init__(self, provider: str, model: Optional[str] = None):
        self.provider = provider
        self._client = None

        if provider == "openai":
            import openai
            self._client = openai.OpenAI()
            self.model = model or "gpt-4o-mini"

        elif provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic()
            self.model = model or "claude-sonnet-4-20250514"

        elif provider == "gemini":
            # Google Gemini uses its own SDK
            self.model = model or "gemini-2.0-flash"

        elif provider == "cohere":
            # Cohere uses its own SDK
            self.model = model or "command-r-plus"

        elif provider == "ollama":
            self.model = model or os.environ.get("OLLAMA_MODEL", "gemma3:4b")

        elif provider in _OPENAI_COMPATIBLE:
            # All OpenAI-compatible providers (Groq, DeepSeek, Together, etc.)
            import openai
            cfg = _OPENAI_COMPATIBLE[provider]
            self._client = openai.OpenAI(
                api_key=os.environ.get(cfg["key_env"]),
                base_url=cfg["base_url"],
            )
            fix_fn = cfg.get("fix_model")
            if fix_fn and model:
                model = fix_fn(model)
            self.model = model or cfg["default_model"]

        else:
            raise ValueError(
                f"Unknown provider: {provider}. "
                f"Use: openai, anthropic, gemini, cohere, ollama, "
                f"{', '.join(_OPENAI_COMPATIBLE.keys())}"
            )

    def generate(self, prompt: str, system: str = "", max_tokens: int = 4096) -> str:
        """Generate a text response. Rate-limited + falls back if primary returns empty."""
        from .rate_limiter import get_limiter
        with get_limiter()(self.provider):
            result = self._generate_inner(prompt, system, max_tokens)
            if not result or not result.strip():
                result = self._try_fallback(prompt, system, max_tokens)
            return result

    def _try_fallback(self, prompt: str, system: str, max_tokens: int) -> str:
        """Try fallback models when primary returns empty."""
        if self.provider == "ollama":
            return ""  # Already on fallback, don't recurse
        # Try other cloud providers first, Ollama only as last resort
        fallbacks = []
        # Cloud providers first (reliable)
        if os.environ.get("OPENAI_API_KEY") and self.provider != "openai":
            fallbacks.append(("openai", "gpt-4o-mini"))
        if os.environ.get("GROQ_API_KEY") and self.provider != "groq":
            fallbacks.append(("groq", "llama-3.3-70b-versatile"))
        if os.environ.get("ANTHROPIC_API_KEY") and self.provider != "anthropic":
            fallbacks.append(("anthropic", "claude-sonnet-4-20250514"))
        # Ollama last resort (may be offline)
        if not fallbacks:
            try:
                import requests
                url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
                tags = requests.get(f"{url}/api/tags", timeout=2).json()
                models = [m["name"] for m in tags.get("models", []) if "embed" not in m["name"]]
                if models:
                    fallbacks.append(("ollama", models[0].split(":")[0]))
            except Exception:
                pass

        for fb_provider, fb_model in fallbacks:
            try:
                fb = LLMClient(provider=fb_provider, model=fb_model)
                result = fb._generate_inner(prompt, system, max_tokens)
                if result and result.strip():
                    logger.info("Fallback to %s:%s succeeded", fb_provider, fb_model)
                    return result
            except Exception:
                continue
        return ""

    def _generate_inner(self, prompt: str, system: str = "", max_tokens: int = 4096) -> str:
        """Inner generate without fallback."""
        if self.provider in ("openai",) or self.provider in _OPENAI_COMPATIBLE:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = self._client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""

        elif self.provider == "anthropic":
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system or "You are a helpful assistant.",
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text

        elif self.provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
            model = genai.GenerativeModel(self.model, system_instruction=system or None)
            resp = model.generate_content(prompt)
            return resp.text

        elif self.provider == "cohere":
            import cohere
            client = cohere.ClientV2(api_key=os.environ.get("COHERE_API_KEY"))
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = client.chat(model=self.model, messages=messages)
            return resp.message.content[0].text

        elif self.provider == "ollama":
            import requests
            url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(f"{url}/api/chat", json={
                "model": self.model, "messages": messages, "stream": False,
                "keep_alive": "30m",
            }, timeout=120)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")

        return ""

    def generate_json(self, prompt: str, system: str = "", max_tokens: int = 4096) -> Dict[str, Any]:
        """Generate a structured JSON response."""
        json_system = system + "\n\nIMPORTANT: Respond with valid JSON only. No markdown, no explanation."

        if self.provider in ("openai",) or self.provider in _OPENAI_COMPATIBLE:
            messages = [
                {"role": "system", "content": json_system},
                {"role": "user", "content": prompt},
            ]
            kwargs: Dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
            # Some providers support response_format, some don't
            try:
                kwargs["response_format"] = {"type": "json_object"}
                resp = self._client.chat.completions.create(**kwargs)
            except Exception:
                del kwargs["response_format"]
                resp = self._client.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content or "{}"
            return _parse_json(text)

        else:
            text = self.generate(prompt, system=json_system, max_tokens=max_tokens)
            return _parse_json(text)


    async def agenerate(self, prompt: str, system: str = "", max_tokens: int = 4096) -> str:
        """Async variant of generate() using httpx. For use in async contexts."""
        try:
            import httpx
        except ImportError:
            # Fallback to sync in thread
            import asyncio
            return await asyncio.get_event_loop().run_in_executor(
                None, self.generate, prompt, system, max_tokens
            )

        from .rate_limiter import get_limiter
        limiter = get_limiter()
        if not limiter.acquire(self.provider, timeout=120):
            return ""
        try:
            if self.provider == "ollama":
                url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(f"{url}/api/generate", json={
                        "model": self.model, "prompt": prompt, "system": system,
                        "stream": False, "options": {"num_predict": max_tokens},
                    })
                    return resp.json().get("response", "")
            else:
                # OpenAI-compatible providers
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})
                base_url = getattr(self._client, '_base_url', None) or ""
                api_key = getattr(self._client, 'api_key', '') or os.environ.get("OPENAI_API_KEY", "")
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        json={"model": self.model, "messages": messages, "max_tokens": max_tokens},
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    )
                    data = resp.json()
                    return data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.warning("Async LLM generate failed: %s", e)
            return ""
        finally:
            limiter.release(self.provider)


def _parse_json(text: str) -> Dict[str, Any]:
    """Parse JSON from LLM output, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return json.loads(text)


def get_llm_client(provider: Optional[str] = None, model: Optional[str] = None) -> LLMClient:
    """Get an LLM client, auto-detecting provider from env vars.

    Priority: explicit > Groq > OpenAI > Anthropic > Gemini > DeepSeek >
              Together > Mistral > Cerebras > Fireworks > Perplexity > Cohere > Ollama
    """
    global _cached_client

    if provider:
        return LLMClient(provider=provider, model=model)

    if _cached_client is not None and model is None:
        return _cached_client

    # Auto-detect by checking env vars in priority order
    detection_order = [
        ("openai", "OPENAI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("gemini", "GOOGLE_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("together", "TOGETHER_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("cerebras", "CEREBRAS_API_KEY"),
        ("fireworks", "FIREWORKS_API_KEY"),
        ("perplexity", "PERPLEXITY_API_KEY"),
        ("cohere", "COHERE_API_KEY"),
    ]

    for prov, env_key in detection_order:
        if os.environ.get(env_key):
            _cached_client = LLMClient(provider=prov, model=model)
            logger.info("LLM client: %s (%s)", _cached_client.provider, _cached_client.model)
            return _cached_client

    # Fallback to Ollama (local, no API key needed)
    _cached_client = LLMClient(provider="ollama", model=model)
    logger.info("LLM client: ollama (%s)", _cached_client.model)
    return _cached_client
