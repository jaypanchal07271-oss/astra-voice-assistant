"""
Automated tests for Spotify music and playlist search & playback automation.
Verifies:
1. Intelligent query cleaning strips noise words while preserving playlist/track intent.
2. Automated playback keystrokes (Enter / PlayPause) trigger for Spotify Desktop.
3. Generic music commands ('play music', 'spotify chalao') trigger play/pause resume.
4. Voice command hybrid interception parses COMMAND: PLAY_SPOTIFY | <query>.
5. Fallback intent parser matches playlist, music, and Hindi song commands.
6. Local executor registers and dispatches play_spotify_music.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from core import actions, brain
import local_executor


def test_play_spotify_music_query_cleaning():
    """Verify noisy query strings are sanitized to clean search queries."""
    with patch("os.startfile") as mock_startfile, \
         patch("pyautogui.press") as mock_press:

        # 1. Noisy Hindi query with playlist
        res = actions.play_spotify_music("spotify pe relax playlist chalao")
        assert res["success"] is True
        assert res["query"] == "relax playlist"
        assert "relax playlist" in res["message"]
        mock_startfile.assert_called_once()
        assert "spotify:search:relax%20playlist" in mock_startfile.call_args[0][0]

        mock_startfile.reset_mock()
        # 2. English phrase with "open" and "and play"
        res2 = actions.play_spotify_music("open spotify and play lofi chill songs")
        assert res2["success"] is True
        assert res2["query"] == "lofi chill"
        mock_startfile.assert_called_once()
        assert "spotify:search:lofi%20chill" in mock_startfile.call_args[0][0]


def test_play_spotify_music_generic_resume():
    """Verify generic 'play music' or empty query triggers play/pause resume."""
    with patch("os.startfile") as mock_startfile, \
         patch("threading.Thread") as mock_thread:

        # Generic "music"
        res = actions.play_spotify_music("play music")
        assert res["success"] is True
        assert "spotify" in res["message"].lower() and "music" in res["message"].lower()
        mock_startfile.assert_called_once_with("spotify:")
        mock_thread.assert_called_once()


def test_play_spotify_music_web_fallback_when_startfile_fails():
    """Verify browser opens when desktop Spotify startfile fails."""
    with patch("os.startfile", side_effect=OSError("App not found")), \
         patch.object(actions, "_launch_browser_url", return_value=True) as mock_launch, \
         patch("threading.Thread") as mock_thread:

        res = actions.play_spotify_music("jujutsu kaisen opening")
        assert res["success"] is True
        assert "Spotify Web" in res["message"]
        mock_launch.assert_called_once()
        assert "open.spotify.com/search/jujutsu+kaisen+opening" in mock_launch.call_args[0][0]
        mock_thread.assert_called_once()


def test_fallback_intent_parser_spotify_music_and_playlists():
    """Verify fallback_intent_parser routes music and playlist commands to Spotify."""
    with patch.object(actions, "play_spotify_music", return_value={"success": True, "message": "Spotify par play kar diya hai."}) as mock_play:
        # 1. "play playlist"
        res1 = brain.fallback_intent_parser("play lofi playlist")
        assert res1["action"]["success"] is True
        assert mock_play.called

        mock_play.reset_mock()
        # 2. "spotify pe gaana chalao"
        res2 = brain.fallback_intent_parser("spotify pe specialz chalao")
        assert res2["action"]["success"] is True
        assert mock_play.called

        mock_play.reset_mock()
        # 3. "play music"
        res3 = brain.fallback_intent_parser("play music")
        assert res3["action"]["success"] is True
        assert mock_play.called


@pytest.mark.asyncio
async def test_process_voice_command_spotify_hybrid_interception():
    """Verify COMMAND: PLAY_SPOTIFY is intercepted and dispatched directly."""
    mock_chat = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "COMMAND: PLAY_SPOTIFY | relaxing piano playlist"
    mock_chat.send_message.return_value = mock_response

    with patch("core.brain.session_manager.get_or_create_chat", return_value=mock_chat), \
         patch.object(actions, "play_spotify_music", return_value={"success": True, "message": "Spotify par relaxing piano playlist play kar diya hai."}) as mock_play, \
         patch.dict("os.environ", {"GEMINI_API_KEY": "test_key"}):

        res = await brain.process_voice_command("play relaxing piano on spotify", session_id="test_spotify_afc")
        assert "relaxing piano playlist" in res["reply"]
        mock_play.assert_called_once_with(query="relaxing piano playlist")


def test_local_executor_supports_spotify_and_instagram_search():
    """Verify local_executor SUPPORTED_TOOLS contains play_spotify_music and search_instagram_user."""
    assert "play_spotify_music" in local_executor.SUPPORTED_TOOLS
    assert "search_instagram_user" in local_executor.SUPPORTED_TOOLS

    with patch.object(actions, "play_spotify_music", return_value={"success": True, "message": "Played"}) as mock_play:
        exec_res = local_executor.execute_local_tool("play_spotify_music", {"query": "lofi"})
        assert exec_res["success"] is True
        mock_play.assert_called_once_with(query="lofi")
