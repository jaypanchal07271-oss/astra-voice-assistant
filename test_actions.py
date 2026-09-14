import unittest
from unittest.mock import patch, MagicMock
from core import actions


class TestSendWhatsAppMessagePlatform(unittest.TestCase):
    @patch("webbrowser.open")
    def test_send_whatsapp_message_mobile(self, mock_browser_open):
        """
        asserts webbrowser.open is called with a whatsapp:// URL when is_mobile=True.
        """
        phone = "919876543210"
        msg = "Hello from mobile device"
        res = actions.send_whatsapp_message(phone_number=phone, message=msg, is_mobile=True)

        self.assertTrue(res.get("success", False))
        self.assertTrue(mock_browser_open.called)
        called_url = mock_browser_open.call_args[0][0]
        self.assertTrue(called_url.startswith("whatsapp://send"))
        self.assertIn(f"phone={phone}", called_url)
        self.assertIn("text=Hello%20from%20mobile%20device", called_url)

    @patch("webbrowser.open")
    @patch("time.sleep")
    @patch("pyautogui.press")
    def test_send_whatsapp_message_desktop(self, mock_press, mock_sleep, mock_browser_open):
        """
        asserts webbrowser.open is called with a https://web.whatsapp.com/send URL
        when is_mobile=False, and that pyautogui.press('enter') is called after the delay.
        """
        phone = "919876543210"
        msg = "Hello from desktop browser"
        res = actions.send_whatsapp_message(phone_number=phone, message=msg, is_mobile=False)

        self.assertTrue(res.get("success", False))
        self.assertTrue(mock_browser_open.called)
        called_url = mock_browser_open.call_args[0][0]
        self.assertTrue(called_url.startswith("https://web.whatsapp.com/send"))
        self.assertIn(f"phone={phone}", called_url)
        self.assertIn("text=Hello%20from%20desktop%20browser", called_url)

        # Assert delay was triggered and enter key pressed
        self.assertTrue(mock_sleep.called)
        mock_press.assert_called_with('enter')


# Pytest-compatible standalone test functions
@patch("webbrowser.open")
def test_send_whatsapp_message_mobile(mock_browser_open):
    phone = "919876543210"
    msg = "Hello from mobile"
    res = actions.send_whatsapp_message(phone_number=phone, message=msg, is_mobile=True)
    assert res.get("success") is True
    assert mock_browser_open.called
    called_url = mock_browser_open.call_args[0][0]
    assert called_url.startswith("whatsapp://send")
    assert f"phone={phone}" in called_url
    assert "text=Hello%20from%20mobile" in called_url


@patch("webbrowser.open")
@patch("time.sleep")
@patch("pyautogui.press")
def test_send_whatsapp_message_desktop(mock_press, mock_sleep, mock_browser_open):
    phone = "919876543210"
    msg = "Hello from desktop"
    res = actions.send_whatsapp_message(phone_number=phone, message=msg, is_mobile=False)
    assert res.get("success") is True
    assert mock_browser_open.called
    called_url = mock_browser_open.call_args[0][0]
    assert called_url.startswith("https://web.whatsapp.com/send")
    assert f"phone={phone}" in called_url
    assert "text=Hello%20from%20desktop" in called_url
    assert mock_sleep.called
    mock_press.assert_called_with('enter')


if __name__ == "__main__":
    unittest.main()

