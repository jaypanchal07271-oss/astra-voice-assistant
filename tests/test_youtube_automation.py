"""
Tests for Structured YouTube Automation (COMMAND: PLAY_YT | <search_query>)
Verifies:
1. Extraction of search topic from COMMAND: PLAY_YT | <query> in AI response.
2. Interception in process_voice_command and dispatch to play_youtube_video.
3. Execution via pywhatkit when available, and resilient fallback to browser URL.
4. Fallback intent parsing for natural Hindi/English YouTube play requests.
5. Multi-line and quoted string handling.
"""

import pytest
from unittest.mock import MagicMock
from core import actions, brain


def test_play_youtube_video_with_pywhatkit(monkeypatch):
    """Verifies that play_youtube_video invokes pywhatkit.playonyt when pywhatkit is present."""
    called_queries = []
    mock_pywhatkit = MagicMock()
    mock_pywhatkit.playonyt.side_effect = lambda q: called_queries.append(q)

    monkeypatch.setitem(__import__("sys").modules, "pywhatkit", mock_pywhatkit)

    res = actions.play_youtube_video("Jujutsu Kaisen theme song")

    assert res["success"] is True
    assert res["action"] == "play_youtube_video"
    assert res["query"] == "Jujutsu Kaisen theme song"
    assert res["method"] == "pywhatkit"
    assert len(called_queries) == 1
    assert called_queries[0] == "Jujutsu Kaisen theme song"
    assert "Jujutsu Kaisen theme song" in res["message"]


def test_play_youtube_video_fallback_without_pywhatkit(monkeypatch):
    """Verifies that if pywhatkit is missing or throws an error, it falls back to launching browser URL."""
    launched_urls = []
    monkeypatch.setattr(actions, "_launch_browser_url", lambda url: launched_urls.append(url))

    # Force pywhatkit import to fail
    monkeypatch.setitem(__import__("sys").modules, "pywhatkit", None)

    res = actions.play_youtube_video("Arijit Singh live")

    assert res["success"] is True
    assert res["action"] == "play_youtube_video"
    assert res["query"] == "Arijit Singh live"
    assert res["method"] in ["direct_watch_url", "browser_url", "search_url"]
    assert len(launched_urls) == 1
    assert "https://www.youtube.com/" in launched_urls[0]


def test_play_youtube_video_empty_query():
    """Verifies that an empty query opens the main YouTube homepage."""
    res = actions.play_youtube_video("")
    assert res["success"] is True
    assert res["action"] == "open_website"
    assert res["url"] == "https://www.youtube.com"


@pytest.mark.asyncio
async def test_process_voice_command_structured_youtube_interception(monkeypatch):
    """
    Verifies that when AI outputs:
    COMMAND: PLAY_YT | Jujutsu Kaisen theme song
    process_voice_command intercepts it, triggers play_youtube_video, and returns audio confirmation.
    """
    played = []
    monkeypatch.setattr(actions, "play_youtube_video", lambda query: played.append(query) or {
        "success": True,
        "action": "play_youtube_video",
        "query": query,
        "message": f"YouTube par '{query}' chala diya hai."
    })

    mock_chat = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "COMMAND: PLAY_YT | Jujutsu Kaisen theme song"
    mock_chat.send_message.return_value = mock_response

    with monkeypatch.context() as m:
        m.setattr("os.getenv", lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d)
        m.setattr("core.brain.session_manager.get_or_create_chat", lambda *a, **kw: mock_chat)

        result = await brain.process_voice_command("play Jujutsu Kaisen theme song on YouTube", session_id="test_yt_interception")

        assert len(played) == 1
        assert played[0] == "Jujutsu Kaisen theme song"
        assert "Jujutsu Kaisen theme song" in result["reply"]
        assert result["action"]["success"] is True


@pytest.mark.asyncio
async def test_process_voice_command_structured_youtube_with_quotes(monkeypatch):
    """Verifies that quotes or markdown in the AI command string are stripped cleanly."""
    played = []
    monkeypatch.setattr(actions, "play_youtube_video", lambda query: played.append(query) or {
        "success": True,
        "action": "play_youtube_video",
        "query": query,
        "message": f"YouTube par '{query}' chala diya hai."
    })

    mock_chat = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '```\nCOMMAND: PLAY_YT | "Believer Imagine Dragons"\n```'
    mock_chat.send_message.return_value = mock_response

    with monkeypatch.context() as m:
        m.setattr("os.getenv", lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d)
        m.setattr("core.brain.session_manager.get_or_create_chat", lambda *a, **kw: mock_chat)

        result = await brain.process_voice_command("play believer on YouTube", session_id="test_yt_quotes")

        assert len(played) == 1
        assert played[0] == "Believer Imagine Dragons"
        assert "Believer Imagine Dragons" in result["reply"]


def test_fallback_intent_parser_youtube_play(monkeypatch):
    """Verifies that natural voice commands in fallback mode route to play_youtube_video."""
    played = []
    monkeypatch.setattr(actions, "play_youtube_video", lambda query: played.append(query) or {
        "success": True,
        "action": "play_youtube_video",
        "query": query,
        "message": f"YouTube par '{query}' chala diya hai."
    })

    res1 = brain.fallback_intent_parser("youtube par shape of you chalao")
    assert len(played) == 1
    assert "shape of you" in played[0].lower()
    assert "shape of you" in res1["reply"].lower()

    res2 = brain.fallback_intent_parser("play lofi beats on youtube")
    assert len(played) == 2
    assert "lofi beats" in played[1].lower()
    assert "lofi beats" in res2["reply"].lower()


def test_fallback_intent_parser_yt_aliases_and_phrasings(monkeypatch):
    """
    Verifies exact user utterances:
    - 'open yt and play specialz song' -> play specialz
    - 'open yt and play specialz' -> play specialz
    - 'play specialz on yt' -> play specialz
    - 'play yt' -> open youtube
    - 'play youtube video' -> open youtube
    - 'play video' -> open youtube
    - 'open yt' -> open youtube
    """
    played = []
    opened = []
    monkeypatch.setattr(actions, "play_youtube_video", lambda query: played.append(query) or {
        "success": True, "action": "play_youtube_video", "query": query, "message": f"Playing {query}"
    })
    monkeypatch.setattr(actions, "open_website", lambda site, query="": opened.append((site, query)) or {
        "success": True, "action": "open_website", "url": "https://www.youtube.com", "message": "Opened"
    })

    # 1. 'open yt and play specialz song'
    res1 = brain.fallback_intent_parser("open yt and play specialz song")
    assert len(played) == 1
    assert played[0] == "specialz"

    # 2. 'open yt and play specialz'
    res2 = brain.fallback_intent_parser("open yt and play specialz")
    assert len(played) == 2
    assert played[1] == "specialz"

    # 3. 'play specialz on yt'
    res3 = brain.fallback_intent_parser("play specialz on yt")
    assert len(played) == 3
    assert played[2] == "specialz"

    # 4. 'play yt' -> bare open
    res4 = brain.fallback_intent_parser("play yt")
    assert len(opened) >= 1
    assert "youtube" in opened[-1][0]

    # 5. 'play youtube video' -> bare open
    res5 = brain.fallback_intent_parser("play youtube video")
    assert len(opened) >= 2
    assert "youtube" in opened[-1][0]

    # 6. 'play video' -> bare open
    res6 = brain.fallback_intent_parser("play video")
    assert len(opened) >= 3
    assert "youtube" in opened[-1][0]

    # 7. 'open yt' -> bare open
    res7 = brain.fallback_intent_parser("open yt")
    assert len(opened) >= 4
    assert "youtube" in opened[-1][0]


def test_fetch_youtube_first_video_url_mock(monkeypatch):
    """Verifies that fetch_youtube_first_video_url extracts watch?v= ID from HTML."""
    mock_html = b'<html><body><a href="/watch?v=dQw4w9WgXcQ">Rick</a></body></html>'
    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_html
    mock_resp.__enter__.return_value = mock_resp

    mock_urlopen = MagicMock(return_value=mock_resp)
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    url = actions.fetch_youtube_first_video_url("rick roll")
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

