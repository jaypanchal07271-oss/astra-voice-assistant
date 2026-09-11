"""
Tests for Real-Time Web Search Q&A Feature and WhatsApp Fail-Proof Fallback
Production-Hardened: Caching, Retries, Normalization, Token Capping.
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock
from core import actions
from core import brain
import mcp_skills


def test_search_web_for_answer_empty_query():
    res = actions.search_web_for_answer("")
    assert res["success"] is False
    assert "Query cannot be empty" in res["error"]


def test_normalize_search_query_hinglish():
    # Test Hinglish weather translation
    assert "weather today" in actions.normalize_search_query("Aaj ka mausam kaisa hai")
    # Test Hinglish price question
    assert "iPhone 16 price" in actions.normalize_search_query("iPhone 16 ki price kya hai")
    # Test conversational filler stripping
    assert "who is prime minister of india" in actions.normalize_search_query("bhai batao who is prime minister of india")
    # Test release date translation
    assert "GTA 6 release date" in actions.normalize_search_query("GTA 6 kab release hoga")


def test_search_web_for_answer_success():
    actions.clear_web_search_cache()
    mock_results = [
        {"title": "Python 3.14 Features", "body": "Python 3.14 introduces several improvements.", "href": "https://python.org"},
        {"title": "What is new in Python", "body": "Overview of modern Python enhancements.", "href": "https://docs.python.org"}
    ]

    with patch("ddgs.DDGS") as MockDDGS:
        mock_instance = MagicMock()
        mock_instance.text.return_value = mock_results
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        MockDDGS.return_value = mock_instance

        res = actions.search_web_for_answer("python 3.14 features")

        assert res["success"] is True
        assert res["action"] == "search_web_for_answer"
        assert res["count"] == 2
        assert "Python 3.14 Features" in res["results"]
        assert "Link: https://python.org" in res["results"]
        assert "(Source: Python)" in res["results"]


def test_search_web_for_answer_ttl_caching():
    actions.clear_web_search_cache()
    mock_results = [{"title": "Cached Title", "body": "Cached Body Content.", "href": "https://example.com"}]

    with patch("ddgs.DDGS") as MockDDGS:
        mock_instance = MagicMock()
        mock_instance.text.return_value = mock_results
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        MockDDGS.return_value = mock_instance

        # Call 1: Fetches from search engine
        res1 = actions.search_web_for_answer("quantum computing 2026")
        assert res1["success"] is True
        assert res1.get("cached") is not True

        # Call 2: Returns instantly from TTL cache
        res2 = actions.search_web_for_answer("quantum computing 2026")
        assert res2["success"] is True
        assert res2.get("cached") is True

        # DDGS should only have been called once despite two function invocations!
        assert mock_instance.text.call_count == 1


def test_search_web_for_answer_context_capping():
    actions.clear_web_search_cache()
    # Provide huge snippets that exceed 1500 chars
    long_body = "A" * 1000
    mock_results = [
        {"title": "Result 1", "body": long_body, "href": "https://example.com/1"},
        {"title": "Result 2", "body": long_body, "href": "https://example.com/2"},
        {"title": "Result 3", "body": long_body, "href": "https://example.com/3"},
    ]

    with patch("ddgs.DDGS") as MockDDGS:
        mock_instance = MagicMock()
        mock_instance.text.return_value = mock_results
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        MockDDGS.return_value = mock_instance

        res = actions.search_web_for_answer("massive text query")
        assert res["success"] is True
        # Must be hard-capped at 1500 chars to avoid LLM context overflow
        assert len(res["results"]) <= 1500


def test_search_web_for_answer_retry_mechanism():
    actions.clear_web_search_cache()
    mock_results = [{"title": "Retry Success", "body": "Recovered after first attempt.", "href": "https://example.com"}]

    with patch("ddgs.DDGS") as MockDDGS:
        mock_instance = MagicMock()
        # Fail first call, succeed on second attempt
        mock_instance.text.side_effect = [Exception("Rate limited 429"), mock_results]
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        MockDDGS.return_value = mock_instance

        with patch("time.sleep"):  # Speed up tests by skipping sleep
            res = actions.search_web_for_answer("query with retry")
            assert res["success"] is True
            assert "Retry Success" in res["results"]
            assert mock_instance.text.call_count == 2


def test_brain_tool_registration():
    assert brain.search_web_for_answer in brain.TOOLS_LIST
    assert "search_web_for_answer" in brain.TOOL_MAP
    assert brain.TOOL_MAP["search_web_for_answer"] == brain.search_web_for_answer

    # Verify tool description
    assert "answers to real-time, factual, or general knowledge questions" in brain.search_web_for_answer.__doc__


def test_brain_search_web_tool_call():
    with patch("core.actions.search_web_for_answer") as mock_act:
        mock_act.return_value = {
            "success": True,
            "results": "[1] Sundar Pichai is CEO of Google."
        }
        res_str = brain.search_web_for_answer("who is CEO of Google")
        assert "Sundar Pichai" in res_str
        mock_act.assert_called_once_with("who is CEO of Google")


@pytest.mark.asyncio
async def test_mcp_web_search_tool_registration():
    tools = await mcp_skills.mcp.list_tools()
    tool_names = {t.name for t in tools}
    assert "web_search_qa" in tool_names

    with patch("core.actions.search_web_for_answer") as mock_act:
        mock_act.return_value = {
            "success": True,
            "results": "[1] Lionel Messi won the 2022 World Cup."
        }
        mcp_res = mcp_skills.web_search_qa("who won 2022 world cup")
        assert "Lionel Messi" in mcp_res


def test_fallback_intent_web_search():
    with patch("core.actions.search_web_for_answer") as mock_act:
        mock_act.return_value = {
            "success": True,
            "results": "[1] AI voice assistant information"
        }
        res = brain.fallback_intent_parser("web search for latest tech news")
        assert res["action"] is not None
        assert res["action"]["success"] is True
        assert "Web search results" in res["reply"]


def test_send_whatsapp_message_failproof_fallback():
    # If UI automation raises error, fallback URL is invoked
    with patch("core.whatsapp_agent.send_whatsapp_message_ui", side_effect=RuntimeError("Window not active")):
        with patch("core.actions._launch_browser_url") as mock_launch:
            res = actions.send_whatsapp_message(contact_name="9876543210", message="Hello world")
            assert res["success"] is True
            assert res.get("fallback_engaged") is True
            assert "919876543210" in res["url"]
            assert mock_launch.called
