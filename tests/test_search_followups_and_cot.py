import pytest
from unittest.mock import patch, MagicMock
from core import brain
from core.brain import (
    parse_search_results_to_options,
    _format_search_reply,
    _clean_chain_of_thought,
    _resolve_search_option_followup,
    session_manager,
    fallback_intent_parser,
)

SAMPLE_SEARCH_RAW = """
[1] Tech News (Source: TechCrunch)
Summary: The tech industry announces new breakthrough chips and advancements in edge AI.
Link: https://techcrunch.com/tech-news

[2] Google News - Technology - Latest (Source: Google)
Summary: Updates on Android, Chrome, and open source development kits for developers worldwide.
Link: https://news.google.com/tech

[3] Apple & Intel Updates (Source: Reuters)
Summary: Apple and Intel announce collaboration on low-power architectures for laptops and mobile devices.
Link: https://reuters.com/apple-intel
""".strip()


def test_parse_search_results_to_options():
    options = parse_search_results_to_options(SAMPLE_SEARCH_RAW)
    assert len(options) == 3
    assert options[1]["title"] == "Tech News"
    assert "breakthrough chips" in options[1]["summary"]
    assert options[1]["link"] == "https://techcrunch.com/tech-news"

    assert options[2]["title"] == "Google News - Technology - Latest"
    assert "Android" in options[2]["summary"]
    assert options[2]["link"] == "https://news.google.com/tech"

    assert options[3]["title"] == "Apple & Intel Updates"
    assert "collaboration" in options[3]["summary"]
    assert options[3]["link"] == "https://reuters.com/apple-intel"


def test_format_search_reply_records_session_options():
    sid = "test_search_session_1"
    session_manager.reset_session(sid)

    reply = _format_search_reply("What are the top tech headlines today?", SAMPLE_SEARCH_RAW, session_id=sid)
    assert "Today's top headlines: 1. Tech News, 2. Google News - Technology - Latest, 3. Apple & Intel Updates." in reply

    stored_opts = session_manager.get_user_data(sid, "last_search_options")
    assert stored_opts is not None
    assert len(stored_opts) == 3


def test_resolve_search_option_by_number():
    sid = "test_search_session_2"
    session_manager.reset_session(sid)
    _format_search_reply("What are the top tech headlines today?", SAMPLE_SEARCH_RAW, session_id=sid)

    # User simply sends "3"
    res = _resolve_search_option_followup("3", session_id=sid)
    assert res is not None
    assert "Apple & Intel Updates" in res["reply"]
    assert "Would you like me to open the full article in your browser?" in res["reply"]
    assert res["action"]["status"] == "search_option_selected"

    # User confirms with "yes"
    with patch("core.actions.open_website") as mock_open:
        mock_open.return_value = {"success": True, "message": "Website opened"}
        confirm_res = _resolve_search_option_followup("yes", session_id=sid)
        assert confirm_res is not None
        assert "Opening #3 (Apple & Intel Updates) in your browser." in confirm_res["reply"]
        mock_open.assert_called_once_with("https://reuters.com/apple-intel")


def test_resolve_search_option_by_headline_string():
    sid = "test_search_session_3"
    session_manager.reset_session(sid)
    _format_search_reply("What are the top tech headlines today?", SAMPLE_SEARCH_RAW, session_id=sid)

    # User sends "3. Google News - Technology - Latest."
    res = _resolve_search_option_followup("3. Google News - Technology - Latest.", session_id=sid)
    assert res is not None
    assert "Google News - Technology - Latest" in res["reply"]
    assert "Android" in res["reply"]
    assert "Would you like me to open the full article in your browser?" in res["reply"]


def test_resolve_search_option_open_command():
    sid = "test_search_session_4"
    session_manager.reset_session(sid)
    _format_search_reply("What are the top tech headlines today?", SAMPLE_SEARCH_RAW, session_id=sid)

    # User sends "open 3"
    with patch("core.actions.open_website") as mock_open:
        mock_open.return_value = {"success": True, "message": "Website opened"}
        res = _resolve_search_option_followup("open 3", session_id=sid)
        assert res is not None
        assert "Opening #3 (Apple & Intel Updates) in your browser." in res["reply"]
        mock_open.assert_called_once_with("https://reuters.com/apple-intel")


def test_fallback_does_not_misinterpret_headline_as_google_search():
    sid = "test_search_session_5"
    session_manager.reset_session(sid)
    _format_search_reply("What are the top tech headlines today?", SAMPLE_SEARCH_RAW, session_id=sid)

    # Calling fallback_intent_parser with "3. Google News - Technology - Latest."
    # should NOT trigger Google Search or say "Google par search kar diya hai"
    res = fallback_intent_parser("3. Google News - Technology - Latest.", session_id=sid)
    assert "Google par search kar diya hai" not in res["reply"]
    assert "Google News - Technology - Latest" in res["reply"]
    assert "Would you like me to open the full article in your browser?" in res["reply"]


def test_clean_chain_of_thought_leak_filtered():
    # Test the exact leaked scratchpad string reported by user
    leaked_cot = (
        '**Clarifying Ambiguity** I\'m grappling with the ambiguity of "3." '
        'The command is too vague. Rule 3 dictates a polite clarification request. '
        'I\'m focusing on crafting a conversational sentence to determine the user\'s intent. '
        'Volume, reminders, counting - the possibilities are wide-ranging. I\'m aiming for a natural phrasing.'
    )
    cleaned = _clean_chain_of_thought(leaked_cot)
    assert "**Clarifying Ambiguity**" not in cleaned
    assert "grappling" not in cleaned
    assert "Rule 3 dictates" not in cleaned
    assert "aiming for a natural phrasing" not in cleaned
    assert cleaned == "Could you please clarify how you would like me to assist you with that?"


def test_clean_chain_of_thought_with_valid_trailing_reply():
    mixed = (
        '**Thinking Process:** The user wants to check headlines.\n\n'
        'Here are the top headlines for today: 1. Tech updates, 2. Global market rise.'
    )
    cleaned = _clean_chain_of_thought(mixed)
    assert "**Thinking Process:**" not in cleaned
    assert "The user wants to check headlines" not in cleaned
    assert "Here are the top headlines for today" in cleaned


def test_explicit_google_command_vs_factual_question():
    # 1. "Who is the CEO of Google?" -> Should NOT trigger Google search intent
    with patch("core.actions.search_web_for_answer") as mock_web_search:
        mock_web_search.return_value = {"success": True, "results": "[1] Sundar Pichai is the CEO of Google."}
        res1 = fallback_intent_parser("Who is the CEO of Google?", session_id="test_g1")
        assert "Google par search kar diya hai" not in res1["reply"]

    # 2. "Search on Google python tutorial" -> Should trigger Google search in English
    with patch("core.actions.open_website") as mock_open:
        mock_open.return_value = {"success": True, "message": "Website opened"}
        res2 = fallback_intent_parser("Search on Google python tutorial", session_id="test_g2")
        assert "Searching Google for 'python tutorial'." in res2["reply"]
