"""
Tests for system_control parameter alignment and fallback intent keyword precision.
Verifies:
1. 'volume badhao' invokes system_control without error.
2. 'screenshot lo' invokes system_control(command='screenshot') and does not trigger screen analysis.
3. 'Notepad kholo dekho jaldi' opens Notepad without being hijacked by 'dekho' into screen analysis.
4. Explicit screen vision queries ('screen dikha', 'screen par kya', 'screen dekho') trigger screen analysis.
5. control_system wrapper and system_control direct execution accept command and action kwargs seamlessly.
"""

from unittest.mock import patch, MagicMock
import pytest
import core.brain as brain
import core.actions as actions
from core.brain import _parse_fallback_intent, control_system


def test_volume_badhao_fallback_intent():
    """Confirms 'volume badhao' invokes system_control with command='volume_up' without error."""
    with patch("core.brain.dispatch_pc_tool_sync") as mock_dispatch:
        mock_dispatch.return_value = {"success": True, "message": "Volume badha diya hai."}
        res = _parse_fallback_intent("volume badhao")

        mock_dispatch.assert_called_once_with("system_control", {"command": "volume_up"})
        assert "volume" in res["reply"].lower()
        assert res["action"]["success"] is True


def test_screenshot_lo_triggers_screenshot_not_screen_analysis():
    """Confirms 'screenshot lo' invokes system_control with command='screenshot' and does NOT trigger analyze_screen."""
    with patch("core.brain.dispatch_pc_tool_sync") as mock_dispatch:
        mock_dispatch.return_value = {"success": True, "message": "Screenshot save ho gaya hai"}
        res = _parse_fallback_intent("screenshot lo")

        mock_dispatch.assert_called_once_with("system_control", {"command": "screenshot"})
        assert "Screenshot" in res["reply"]


def test_notepad_kholo_dekho_jaldi_opens_notepad():
    """
    Confirms 'Notepad kholo dekho jaldi' containing 'dekho' opens Notepad
    and is NOT hijacked into screen analysis.
    """
    with patch("core.brain.dispatch_pc_tool_sync") as mock_dispatch:
        mock_dispatch.return_value = {"success": True, "app": "notepad", "message": "notepad khol diya hai."}
        res = _parse_fallback_intent("Notepad kholo dekho jaldi")

        mock_dispatch.assert_called_once_with("open_app", {"app_name": "notepad"})
        assert "notepad" in res["reply"].lower()


def test_explicit_screen_vision_triggers_analyze_screen():
    """Confirms explicit screen queries trigger analyze_screen."""
    explicit_queries = [
        "screen par kya chal raha hai",
        "screen dikhao",
        "screen check karo",
        "screen dekho",
        "analyze screen"
    ]
    for q in explicit_queries:
        with patch("core.brain.dispatch_pc_tool_sync") as mock_dispatch:
            mock_dispatch.return_value = {"success": True, "message": "Screen analyzed"}
            res = _parse_fallback_intent(q)
            mock_dispatch.assert_called_once_with("analyze_screen", {"prompt": q})
            assert "Screen analyzed" in res["reply"]


def test_control_system_wrapper_sends_command_param():
    """Confirms control_system wrapper sends {'command': action} to dispatch_pc_tool_sync."""
    with patch("core.brain.dispatch_pc_tool_sync") as mock_dispatch:
        mock_dispatch.return_value = {"success": True, "message": "Workstation lock kar diya hai."}
        reply = control_system("lock_screen")
        mock_dispatch.assert_called_once_with("system_control", {"command": "lock_screen"})
        assert "Workstation lock" in reply


def test_system_control_accepts_both_command_and_action():
    """Confirms core.actions.system_control accepts command and action keyword arguments."""
    with patch("core.actions.pyautogui.press") as mock_press:
        # Test command="volume_up"
        res1 = actions.system_control(command="volume_up")
        assert res1["success"] is True
        assert res1["command"] == "volume_up"

        # Test action="volume_up"
        res2 = actions.system_control(action="volume_up")
        assert res2["success"] is True
        assert res2["command"] == "volume_up"

