"""
Unit tests for Cloud and Local LLM Provider integration (Gemini, Vertex AI, Ollama).
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

import cloud_llm
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_provider_detection():
    """Verify provider detection priority order."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}):
        assert cloud_llm.get_active_provider_name() == "gemini"

    with patch.dict("os.environ", {"GEMINI_API_KEY": "", "OPENAI_API_KEY": "test-openai-key"}, clear=False):
        assert cloud_llm.get_active_provider_name() == "openai"


def test_gemini_api_success():
    """Verify Gemini API generates narrative text when GEMINI_API_KEY is configured."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Max Verstappen secured victory at the 2021 Abu Dhabi Grand Prix."}]
                }
            }
        ]
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-api-key"}), \
         patch("requests.post", return_value=mock_response):

        res = cloud_llm.generate_ai_text("Who won Abu Dhabi 2021?", preferred_provider="gemini")
        assert res["success"] is True
        assert res["provider"] == "gemini"
        assert "Max Verstappen" in res["text"]
        assert res["latency_seconds"] >= 0


def test_vertex_ai_success():
    """Verify Vertex AI calls generateContent endpoint with Bearer auth."""
    mock_creds = MagicMock()
    mock_creds.token = "fake-gcp-bearer-token"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Lewis Hamilton dominated the 2020 Turkish Grand Prix in the wet."}]
                }
            }
        ]
    }

    with patch.dict("os.environ", {"GCP_PROJECT_ID": "my-gcp-proj", "GCP_REGION": "us-central1"}), \
         patch("google.auth.default", return_value=(mock_creds, "my-gcp-proj")), \
         patch("requests.post", return_value=mock_response):

        res = cloud_llm.generate_ai_text("Tell me about Turkey 2020", preferred_provider="vertex")
        assert res["success"] is True
        assert res["provider"] == "vertex"
        assert "Lewis Hamilton" in res["text"]


def test_fallback_to_ollama_when_cloud_fails():
    """Verify fallback cascade switches to Ollama if cloud LLM raises error."""
    mock_ollama_resp = MagicMock()
    mock_ollama_resp.status_code = 200
    mock_ollama_resp.json.return_value = {"response": "Local Ollama generation succeeded."}

    with patch.dict("os.environ", {"GEMINI_API_KEY": "broken-key"}), \
         patch("cloud_llm._call_gemini_api", side_effect=RuntimeError("Cloud API 403 Forbidden")), \
         patch("cloud_llm._call_vertex_ai", side_effect=RuntimeError("Vertex unconfigured")), \
         patch("requests.post", return_value=mock_ollama_resp):

        res = cloud_llm.generate_ai_text("Simulate season", preferred_provider="gemini")
        assert res["success"] is True
        assert res["provider"] == "ollama"
        assert "Local Ollama" in res["text"]


def test_graceful_handling_all_providers_offline():
    """Verify system does not crash when all cloud and local providers fail."""
    with patch("cloud_llm._call_gemini_api", side_effect=RuntimeError("Gemini down")), \
         patch("cloud_llm._call_vertex_ai", side_effect=RuntimeError("Vertex down")), \
         patch("cloud_llm._call_ollama", side_effect=RuntimeError("Ollama connection refused")):

        res = cloud_llm.generate_ai_text("Test query")
        assert res["success"] is False
        assert res["text"] == ""
        assert res["provider"] == "none"
        assert res["error"] is not None


def test_api_llm_status_endpoint(client):
    """Verify /api/llm/status endpoint responds with status and provider metadata."""
    resp = client.get("/api/llm/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_provider" in data
    assert "configured_keys" in data
    assert "latency_seconds" in data
