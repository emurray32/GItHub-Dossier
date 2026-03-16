"""
Shared LLM Client — single module for all LLM access across the application.

Supports Gemini Flash (primary) and Replit AI proxy / OpenAI (fallback).
Client is cached after first initialization. Thread-safe — returns provider
info as values, not via mutable globals.
"""
import logging
import os
import threading
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Model constants — single source of truth
GEMINI_MODEL = 'gemini-2.5-flash'
OPENAI_MODEL = 'gpt-5-mini'

# Cached client (initialized once, reused across calls)
_client_cache: Optional[Tuple[str, object, str]] = None  # (provider, client, model)
_cache_lock = threading.Lock()
_cache_initialized = False

# SDK availability
try:
    from google import genai
    _GEMINI_AVAILABLE = True
except ImportError:
    genai = None
    _GEMINI_AVAILABLE = False

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    _OPENAI_AVAILABLE = False


def _init_client() -> Optional[Tuple[str, object, str]]:
    """Initialize and cache the LLM client. Thread-safe, runs once."""
    global _client_cache, _cache_initialized

    with _cache_lock:
        if _cache_initialized:
            return _client_cache

        # 1. Gemini Flash (primary)
        gemini_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        if gemini_key and _GEMINI_AVAILABLE:
            _client_cache = ('gemini', genai.Client(api_key=gemini_key), GEMINI_MODEL)
            _cache_initialized = True
            logger.info("[LLM] Initialized Gemini Flash client")
            return _client_cache

        # 2. Replit AI proxy (OpenAI-compatible, fallback)
        base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL')
        api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY')
        if base_url and api_key and _OPENAI_AVAILABLE:
            _client_cache = ('openai', OpenAI(base_url=base_url, api_key=api_key), OPENAI_MODEL)
            _cache_initialized = True
            logger.info("[LLM] Initialized OpenAI/Replit proxy client")
            return _client_cache

        _cache_initialized = True
        _client_cache = None
        logger.info("[LLM] No LLM provider available — will use template fallback")
        return None


def get_llm_client() -> Optional[Tuple[str, object, str]]:
    """Return (provider, client, model) or None. Cached after first call."""
    if not _cache_initialized:
        return _init_client()
    return _client_cache


def llm_generate(system_prompt: str, user_prompt: str) -> Optional[str]:
    """Call the LLM and return the raw text response, or None on failure.

    Returns:
        The LLM response text, or None if no LLM is available or the call fails.
    """
    result = get_llm_client()
    if not result:
        return None

    provider, client, model = result

    try:
        if provider == 'gemini':
            response = client.models.generate_content(
                model=model,
                contents=f"{system_prompt}\n\n---\n\n{user_prompt}",
            )
            return response.text
        else:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )
            return response.choices[0].message.content
    except Exception as e:
        logger.error("[LLM] Generation failed (%s/%s): %s", provider, model, e)
        return None


def get_active_provider() -> str:
    """Return the active provider name ('gemini', 'openai', or 'template')."""
    result = get_llm_client()
    return result[0] if result else 'template'


def get_active_model() -> str:
    """Return the active model name."""
    result = get_llm_client()
    return result[2] if result else 'template'
