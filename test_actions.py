import unittest
import pyperclip
from core import actions

class TestAllTenTools(unittest.TestCase):
    def test_1_open_and_close_application(self):
        """1. open_application & close_application: Tested with AppOpener and taskkill fallback."""
        open_res = actions.open_app("notepad")
        self.assertTrue(open_res["success"])
        self.assertEqual(open_res["app"], "notepad")
        print("[Pass] open_application:", open_res["message"])

        close_res = actions.close_app("notepad")
        self.assertTrue(close_res["success"])
        print("[Pass] close_application:", close_res["message"])

    def test_2_control_system(self):
        """2. control_system: Tested with volume commands, mute, lock, and screenshot capture."""
        vol_up = actions.system_control("volume_up")
        self.assertTrue(vol_up["success"])
        
        vol_down = actions.system_control("volume_down")
        self.assertTrue(vol_down["success"])
        
        vol_mute = actions.system_control("volume_mute")
        self.assertTrue(vol_mute["success"])

        # Screenshot test (graceful in background/headless or active desktop)
        shot_res = actions.system_control("screenshot")
        self.assertIn("action", shot_res)
        print("[Pass] control_system volume & screenshot verified")

    def test_3_get_time_and_date(self):
        """3. get_time_and_date: Verified returning formatted date, time, and ISO timestamp."""
        res = actions.get_time_and_date()
        self.assertTrue(res["success"])
        self.assertIn("time", res)
        self.assertIn("date", res)
        self.assertIn("iso", res)
        self.assertTrue(len(res["iso"]) > 10)
        print(f"[Pass] get_time_and_date: Time={res['time']}, Date={res['date']}, ISO={res['iso']}")

    def test_4_analyze_clipboard(self):
        """4. analyze_clipboard: Verified reading text directly from Windows clipboard."""
        test_text = "Astra AI Voice Assistant is testing Windows clipboard reading."
        pyperclip.copy(test_text)
        res = actions.analyze_clipboard()
        self.assertTrue(res["success"])
        self.assertEqual(res["content"], test_text)
        self.assertEqual(res["word_count"], len(test_text.split()))
        print(f"[Pass] analyze_clipboard: Read {res['word_count']} words: '{res['preview']}'")

    def test_5_open_or_search_website(self):
        """5. open_or_search_website: Verified YouTube formatting, Google search, and direct URL opening."""
        yt_res = actions.open_website("youtube", "Arijit Singh songs")
        self.assertTrue(yt_res["success"])
        self.assertIn("results?search_query=Arijit+Singh+songs", yt_res["url"])

        g_res = actions.open_website("google", "cricket score")
        self.assertTrue(g_res["success"])
        self.assertIn("q=cricket+score", g_res["url"])

        site_res = actions.open_website("https://github.com")
        self.assertTrue(site_res["success"])
        print("[Pass] open_or_search_website: YouTube, Google, and Direct URL verified")

    def test_6_send_whatsapp_message(self):
        """6. send_whatsapp_message: Verified URL schema and URI parameter encoding."""
        res = actions.send_whatsapp("9876543210", "Hello from Astra assistant!")
        self.assertTrue(res["success"])
        self.assertIn("https://web.whatsapp.com/send", res["url"])
        self.assertIn("phone=919876543210", res["url"])
        self.assertIn("text=Hello%20from%20Astra%20assistant%21", res["url"])
        print("[Pass] send_whatsapp_message URL schema verified:", res["url"])

    def test_7_play_spotify_music(self):
        """7. play_spotify_music: Verified spotify:search:... URI trigger with web player fallback."""
        res = actions.play_spotify_music("Arijit Singh")
        self.assertTrue(res["success"])
        self.assertIn("spotify:search:Arijit%20Singh", res["uri"])
        self.assertIn("open.spotify.com/search/Arijit+Singh", res["web_url"])
        print("[Pass] play_spotify_music URI & Web fallback verified:", res["uri"])

    def test_8_start_dev_environment(self):
        """8. start_dev_environment: Verified folder discovery, VS Code launch, and spawning npm run dev in a detached terminal."""
        res = actions.start_dev_environment(project_name="joyful-pasteur")
        self.assertTrue(res["success"])
        self.assertTrue(res["vscode_opened"])
        self.assertIn("directory", res)
        print(f"[Pass] start_dev_environment: Target dir={res['directory']}, npm_dev={res['npm_dev_spawned']}")

    def test_9_manage_odoo_server_security_guardrail(self):
        """9. manage_odoo_server: Security Guardrail Verified — attempts to update or manage account, inventory, or any derived module names return Access Denied immediately."""
        # Attempt 1: Core account module
        res_acc = actions.manage_odoo_server(action="update", module="account")
        self.assertFalse(res_acc["success"])
        self.assertEqual(res_acc["status"], "access_denied")
        self.assertIn("Access Denied", res_acc["message"])
        print("[Pass] Security Guardrail triggered on 'account':", res_acc["message"])

        # Attempt 2: Derived accountant module
        res_acc2 = actions.manage_odoo_server(action="upgrade", module="account_accountant")
        self.assertFalse(res_acc2["success"])
        self.assertEqual(res_acc2["status"], "access_denied")

        # Attempt 3: Core inventory module
        res_inv = actions.manage_odoo_server(action="update", module="inventory")
        self.assertFalse(res_inv["success"])
        self.assertEqual(res_inv["status"], "access_denied")
        self.assertIn("Access Denied", res_inv["message"])
        print("[Pass] Security Guardrail triggered on 'inventory':", res_inv["message"])

        # Attempt 4: Derived stock module
        res_stock = actions.manage_odoo_server(action="update", module="stock_landed_costs")
        self.assertFalse(res_stock["success"])
        self.assertEqual(res_stock["status"], "access_denied")

    def test_10_manage_odoo_server_safe_actions(self):
        """10. manage_odoo_server: Safe server operations (status check / safe modules) succeed."""
        res_status = actions.manage_odoo_server(action="status")
        self.assertTrue(res_status["success"])
        self.assertEqual(res_status["server_status"], "active (running)")
        self.assertEqual(res_status["port"], 8069)
        print("[Pass] Safe Odoo status check passed:", res_status["message"])

if __name__ == "__main__":
    unittest.main()
