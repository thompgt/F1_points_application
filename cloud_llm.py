"""
Unified Cloud and Local LLM Provider for F1 Analytics and Season Simulation.

Supports:
1. Google Gemini Developer API (via GEMINI_API_KEY) - gemini-2.0-flash
2. Google Cloud Vertex AI (via GCP_PROJECT_ID & Application Default Credentials)
3. OpenAI API (via OPENAI_API_KEY)
4. Local Ollama (via OLLAMA_BASE_URL, default: http://localhost:11434)
5. Graceful fallback when all providers are offline
"""

from __future__ import annotations

import os
import time
import logging
from typing import Any, Dict, Optional
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("f1_calculator.cloud_llm")

# Try to import google.auth for Vertex AI
try:
    import google.auth
    from google.auth.transport.requests import Request as GoogleAuthRequest
    GOOGLE_AUTH_AVAILABLE = True
except ImportError:
    GOOGLE_AUTH_AVAILABLE = False
    google = None
    GoogleAuthRequest = None


def get_active_provider_name() -> str:
    """Determine the highest priority LLM provider configured in the environment."""
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("USE_VERTEX_AI", "false").lower() in ("true", "1") or (
        os.getenv("GCP_PROJECT_ID") and GOOGLE_AUTH_AVAILABLE and not os.getenv("PREFER_LOCAL_OLLAMA")
    ):
        return "vertex"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "ollama"


def _call_gemini_api(prompt: str, system_instruction: Optional[str] = None, timeout: float = 20.0) -> str:
    """Call Google Gemini Developer API using GEMINI_API_KEY."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    contents = []
    if system_instruction:
        contents.append({"role": "user", "parts": [{"text": f"System context: {system_instruction}"}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.4")),
            "maxOutputTokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
        }
    }

    response = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"Gemini API returned status {response.status_code}: {response.text[:300]}")

    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini API returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise RuntimeError("Gemini candidate content has no parts")

    return parts[0].get("text", "").strip()


def _call_vertex_ai(prompt: str, system_instruction: Optional[str] = None, timeout: float = 20.0) -> str:
    """Call Google Cloud Vertex AI Gemini endpoint using ambient GCP credentials."""
    if not GOOGLE_AUTH_AVAILABLE:
        raise RuntimeError("google-auth package is required for Vertex AI")

    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise ValueError("GCP_PROJECT_ID is not configured")

    region = os.getenv("GCP_REGION", "us-central1")
    model = os.getenv("VERTEX_MODEL", "gemini-2.0-flash")

    # Acquire GCP OAuth access token
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(GoogleAuthRequest())
    token = credentials.token

    url = (
        f"https://{region}-aiplatform.googleapis.com/v1/projects/{project_id}/"
        f"locations/{region}/publishers/google/models/{model}:generateContent"
    )

    contents = []
    if system_instruction:
        contents.append({"role": "user", "parts": [{"text": f"Instructions: {system_instruction}"}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.4")),
            "maxOutputTokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
        }
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"Vertex AI returned status {response.status_code}: {response.text[:300]}")

    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Vertex AI returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise RuntimeError("Vertex AI candidate has no parts")

    return parts[0].get("text", "").strip()


def _call_openai_api(prompt: str, system_instruction: Optional[str] = None, timeout: float = 20.0) -> str:
    """Call OpenAI or OpenAI-compatible cloud endpoint."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.4")),
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"OpenAI API returned status {response.status_code}: {response.text[:300]}")

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("OpenAI API returned no choices")

    return choices[0].get("message", {}).get("content", "").strip()


def _call_ollama(prompt: str, system_instruction: Optional[str] = None, timeout: float = 60.0) -> str:
    """Call local Ollama server."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt

    response = requests.post(
        f"{base_url}/api/generate",
        json={"model": model, "prompt": full_prompt, "stream": False},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Ollama returned status {response.status_code}: {response.text[:300]}")

    text = response.json().get("response", "").strip()
    if not text:
        raise RuntimeError("Ollama returned empty response")

    return text


def generate_ai_text(
    prompt: str,
    system_instruction: Optional[str] = None,
    preferred_provider: Optional[str] = None,
    timeout: float = 25.0,
) -> Dict[str, Any]:
    """Generate narrative text using the best available Cloud or Local LLM.

    Tries providers in sequence:
    1. Gemini API (if GEMINI_API_KEY)
    2. Vertex AI (if GCP credentials)
    3. OpenAI (if OPENAI_API_KEY)
    4. Ollama (local)

    Returns a dict with: text, provider, model, latency_seconds, success, error.
    """
    start_time = time.perf_counter()
    errors: Dict[str, str] = {}

    provider_order = []
    if preferred_provider:
        provider_order.append(preferred_provider)

    # Add standard fallback cascade
    for p in ["gemini", "vertex", "openai", "ollama"]:
        if p not in provider_order:
            provider_order.append(p)

    for provider in provider_order:
        try:
            if provider == "gemini" and os.getenv("GEMINI_API_KEY"):
                text = _call_gemini_api(prompt, system_instruction, timeout=min(timeout, 15.0))
                return {
                    "text": text,
                    "provider": "gemini",
                    "model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
                    "latency_seconds": round(time.perf_counter() - start_time, 3),
                    "success": True,
                    "error": None,
                }

            elif provider == "vertex" and (
                os.getenv("USE_VERTEX_AI", "false").lower() in ("true", "1") or os.getenv("GCP_PROJECT_ID")
            ):
                text = _call_vertex_ai(prompt, system_instruction, timeout=min(timeout, 15.0))
                return {
                    "text": text,
                    "provider": "vertex",
                    "model": os.getenv("VERTEX_MODEL", "gemini-2.0-flash"),
                    "latency_seconds": round(time.perf_counter() - start_time, 3),
                    "success": True,
                    "error": None,
                }

            elif provider == "openai" and os.getenv("OPENAI_API_KEY"):
                text = _call_openai_api(prompt, system_instruction, timeout=min(timeout, 15.0))
                return {
                    "text": text,
                    "provider": "openai",
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "latency_seconds": round(time.perf_counter() - start_time, 3),
                    "success": True,
                    "error": None,
                }

            elif provider == "ollama":
                # Only try Ollama if requested or if no cloud provider succeeded
                text = _call_ollama(prompt, system_instruction, timeout=timeout)
                return {
                    "text": text,
                    "provider": "ollama",
                    "model": os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
                    "latency_seconds": round(time.perf_counter() - start_time, 3),
                    "success": True,
                    "error": None,
                }

        except Exception as e:
            logger.warning(f"LLM provider '{provider}' failed: {e}")
            errors[provider] = str(e)

    # All providers failed or were unconfigured
    return {
        "text": "",
        "provider": "none",
        "model": "none",
        "latency_seconds": round(time.perf_counter() - start_time, 3),
        "success": False,
        "error": "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else "No LLM provider configured",
    }


def test_llm_status() -> Dict[str, Any]:
    """Test LLM connectivity and report provider availability."""
    probe = generate_ai_text(
        prompt="Reply with exactly: 'F1 telemetry & simulation engine ready.'",
        timeout=5.0,
    )
    return {
        "active_provider": probe["provider"],
        "model": probe["model"],
        "available": probe["success"],
        "latency_seconds": probe["latency_seconds"],
        "error": probe["error"],
        "configured_keys": {
            "gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
            "gcp_project_id": bool(os.getenv("GCP_PROJECT_ID")),
            "openai_api_key": bool(os.getenv("OPENAI_API_KEY")),
            "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        }
    }
