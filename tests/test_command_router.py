"""
Unit and Integration Tests for Command Router & WhatsApp Isolated Pipeline.
Ensures strict routing hierarchy, conjunction immunity, settings separation,
and safe multi-turn conversation flow.
"""

import pytest
from unittest.mock import MagicMock, patch
from core.command_router import (
    CommandRouter,
    extract_whatsapp_parameters,
    clean_compound_clauses,
    CONJUNCTIONS,
    NON_CONTACT_WORDS
)
from core import actions, brain, whatsapp_agent


class TestCommandRouterUnit:
    """Test unit-level regex, parameter extraction, and intent classification."""

    def test_clean_compound_clauses(self):
        assert clean_compound_clauses("WhatsApp open karo aur Rahul ko hello bhejo") == "rahul ko hello bhejo"
        assert clean_compound_clauses("WhatsApp open karo and Rahul ko hello bhejo") == "rahul ko hello bhejo"
        assert clean_compound_clauses("WhatsApp kholo then Rahul ko message karo") == "rahul ko message karo"
        assert clean_compound_clauses("Open WhatsApp and send hello to Rahul") == "send hello to rahul"

    def test_extract_whatsapp_parameters_compound_and_conjunctions(self):
        # 1. WhatsApp par Rahul ko hello bhejo
        c, m, miss = extract_whatsapp_parameters("WhatsApp par Rahul ko hello bhejo")
        assert c == "rahul"
        assert m == "hello"
        assert not miss

        # 2. WhatsApp pe Rahul ko kya kar rahe ho message bhejo
        c, m, miss = extract_whatsapp_parameters("WhatsApp pe Rahul ko kya kar rahe ho message bhejo")
        assert c == "rahul"
        assert m == "kya kar rahe ho"
        assert not miss

        # 3. WhatsApp kholo aur Rahul ko kal milte hain bhejo
        c, m, miss = extract_whatsapp_parameters("WhatsApp kholo aur Rahul ko kal milte hain bhejo")
        assert c == "rahul"
        assert m == "kal milte hain"
        assert not miss

        # 4. WhatsApp open karo then Rahul ko hello send karo
        c, m, miss = extract_whatsapp_parameters("WhatsApp open karo then Rahul ko hello send karo")
        assert c == "rahul"
        assert m == "hello"
        assert not miss

        # 5. WhatsApp me Rahul ko message karo (Missing message)
        c, m, miss = extract_whatsapp_parameters("WhatsApp me Rahul ko message karo")
        assert c == "rahul"
        assert miss is True

        # 6. Compound with 'and' - MUST NOT extract 'And' as contact
        c, m, miss = extract_whatsapp_parameters("WhatsApp open karo and Rahul ko hello bhejo")
        assert c == "rahul"
        assert m == "hello"
        assert c != "and"

    def test_conjunction_rejection_as_contact(self):
        for conj in CONJUNCTIONS:
            c, m, miss = extract_whatsapp_parameters(f"WhatsApp par {conj} ko message bhejo")
            assert c is None

    def test_route_command_hierarchy(self):
        # Priority 1: WhatsApp
        res1 = CommandRouter.route_command("WhatsApp par Rahul ko hello bhejo")
        assert res1["intent"] == "whatsapp_message"
        assert res1["contact"].lower() == "rahul"
        assert res1["message"] == "hello"
        assert res1["is_terminal"] is True

        res2 = CommandRouter.route_command("WhatsApp me Rahul ko message karo")
        assert res2["intent"] == "whatsapp_clarification_needed"
        assert res2["contact"] == "Rahul"
        assert res2["is_terminal"] is True

        res3 = CommandRouter.route_command("WhatsApp kholo")
        assert res3["intent"] == "open_whatsapp"
        assert res3["is_terminal"] is True

        # Priority 2: Windows Settings
        res_set1 = CommandRouter.route_command("Settings kholo")
        assert res_set1["intent"] == "open_settings"
        assert res_set1["setting_target"] == "settings"
        assert res_set1["is_terminal"] is True

        res_set2 = CommandRouter.route_command("WiFi settings kholo")
        assert res_set2["intent"] == "open_settings"
        assert res_set2["setting_target"] == "wifi settings"
        assert res_set2["is_terminal"] is True

        res_set3 = CommandRouter.route_command("Bluetooth settings kholo")
        assert res_set3["intent"] == "open_settings"
        assert res_set3["setting_target"] == "bluetooth settings"
        assert res_set3["is_terminal"] is True

        # Priority 3: Application Launch
        res_app1 = CommandRouter.route_command("Chrome kholo")
        assert res_app1["intent"] == "open_app"

        res_app2 = CommandRouter.route_command("YouTube kholo")
        assert res_app2["intent"] == "open_app"

        res_app3 = CommandRouter.route_command("Calculator kholo")
        assert res_app3["intent"] == "open_app"

        # Priority 4: Web Search
        res_srch = CommandRouter.route_command("Naruto season 3 release date search karo")
        assert res_srch["intent"] == "web_search"
        assert "naruto season 3 release date" in res_srch["query"]
        assert res_srch["is_terminal"] is True


class TestWhatsAppAgentDefensiveGuard:
    """Test that whatsapp_agent rejects conjunctions even if directly invoked."""

    def test_send_whatsapp_message_ui_rejects_conjunction(self):
        res = whatsapp_agent.send_whatsapp_message_ui("And", "Hello")
        assert res["success"] is False
        assert "Conjunctions cannot be used as contact names" in res["error"]

    def test_send_whatsapp_message_ui_rejects_empty_contact(self):
        res = whatsapp_agent.send_whatsapp_message_ui("", "Hello")
        assert res["success"] is False
        assert "cannot be empty" in res["error"]


class TestBrainFallbackRoutingIntegration:
    """Test full integration in brain.fallback_intent_parser."""

    def test_whatsapp_commands_never_trigger_settings_or_search(self, monkeypatch):
        mock_send_whatsapp = MagicMock(return_value={"success": True, "message": "Dispatched"})
        mock_open_app = MagicMock(return_value={"success": True, "message": "Opened"})
        mock_web_search = MagicMock(return_value={"success": True, "results": "Results"})

        monkeypatch.setattr(actions, "send_whatsapp_message", mock_send_whatsapp)
        monkeypatch.setattr(actions, "open_app", mock_open_app)
        monkeypatch.setattr(actions, "search_web_for_answer", mock_web_search)

        whatsapp_cmds = [
            "WhatsApp par Rahul ko hello bhejo",
            "WhatsApp pe Rahul ko kya kar rahe ho message bhejo",
            "WhatsApp kholo aur Rahul ko kal milte hain bhejo",
            "WhatsApp open karo then Rahul ko hello send karo",
            "WhatsApp open karo and Rahul ko hello bhejo"
        ]

        for cmd in whatsapp_cmds:
            mock_send_whatsapp.reset_mock()
            mock_open_app.reset_mock()
            mock_web_search.reset_mock()

            res = brain.fallback_intent_parser(cmd, session_id="test_iso")
            assert mock_send_whatsapp.called
            assert not mock_open_app.called
            assert not mock_web_search.called
            assert "rahul" in res["reply"].lower()

    def test_whatsapp_missing_message_clarification(self, monkeypatch):
        session_id = "test_clarif_sess"
        brain.session_manager.reset_session(session_id)

        res = brain.fallback_intent_parser("WhatsApp me Rahul ko message karo", session_id=session_id)
        assert res["reply"] == "What message would you like to send to Rahul?"
        assert brain.session_manager.get_pending_whatsapp(session_id) == "Rahul"

        # Follow-up: User gives message content
        sent_messages = []
        monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent_messages.append((contact_name, message)) or {"success": True, "message": "Sent"})

        res2 = brain.fallback_intent_parser("Kal subah meeting hai", session_id=session_id)
        assert len(sent_messages) == 1
        assert sent_messages[0] == ("Rahul", "Kal subah meeting hai")
        assert "Rahul" in res2["reply"]
        assert brain.session_manager.get_pending_whatsapp(session_id) is None

    def test_conjunction_as_pending_is_purged(self, monkeypatch):
        session_id = "test_purged_sess"
        brain.session_manager.reset_session(session_id)
        brain.session_manager.record_pending_whatsapp(session_id, "And")

        sent_messages = []
        monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent_messages.append((contact_name, message)) or {"success": True, "message": "Sent"})

        res = brain.fallback_intent_parser("Chrome kholo", session_id=session_id)
        # Should NOT have sent "Chrome kholo" to "And"
        assert len(sent_messages) == 0
        assert brain.session_manager.get_pending_whatsapp(session_id) is None

    def test_windows_settings_commands_route_correctly(self, monkeypatch):
        opened_apps = []
        monkeypatch.setattr(actions, "open_app", lambda app_name: opened_apps.append(app_name) or {"success": True, "message": "Opened"})

        brain.fallback_intent_parser("Settings kholo", session_id="test_s")
        assert "settings" in opened_apps

        brain.fallback_intent_parser("WiFi settings kholo", session_id="test_s")
        assert "wifi settings" in opened_apps

        brain.fallback_intent_parser("Bluetooth settings kholo", session_id="test_s")
        assert "bluetooth settings" in opened_apps

    def test_web_search_command_routes_correctly(self, monkeypatch):
        searched_queries = []
        monkeypatch.setattr(actions, "search_web_for_answer", lambda query: searched_queries.append(query) or {"success": True, "results": "Release date is Oct 2026"})

        res = brain.fallback_intent_parser("Naruto season 3 release date search karo", session_id="test_s")
        assert len(searched_queries) == 1
        assert "naruto season 3 release date" in searched_queries[0]
        assert "Oct 2026" in res["reply"]

    def test_whatsapp_multi_word_contact_and_conjunction(self, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent_messages.append((contact_name, message)) or {"success": True, "message": "Sent"})

        # Case 1: Multi-word contact with message
        res1 = brain.fallback_intent_parser("WhatsApp par Aarti School ko message bhejo hello", session_id="test_m1")
        assert len(sent_messages) == 1
        assert sent_messages[0][0].lower() == "aarti school"
        assert sent_messages[0][1] == "hello"

        # Case 2: Multi-word contact with conjunction payload
        res2 = brain.fallback_intent_parser("WhatsApp par Aarti School ko message bhejo aur bolo kal milte hain", session_id="test_m2")
        assert len(sent_messages) == 2
        assert sent_messages[1][0].lower() == "aarti school"
        assert sent_messages[1][1] == "kal milte hain"

        # Case 3: English variation with multi-word contact
        res3 = brain.fallback_intent_parser("Send a WhatsApp message to Aarti School saying hello", session_id="test_m3")
        assert len(sent_messages) == 3
        assert sent_messages[2][0].lower() == "aarti school"
        assert sent_messages[2][1] == "hello"

        # Case 4: English direct greeting
        res4 = brain.fallback_intent_parser("Send WhatsApp message to Aarti School hello", session_id="test_m4")
        assert len(sent_messages) == 4
        assert sent_messages[3][0].lower() == "aarti school"
        assert sent_messages[3][1] == "hello"

    def test_indic_devanagari_voice_commands(self, monkeypatch):
        from core.command_router import transliterate_indic_command
        # 1. Transliteration unit tests
        assert transliterate_indic_command("ओपन ए व्हाट्सएप") == "open a whatsapp"
        assert transliterate_indic_command("ओपन इंस्टाग्राम") == "open instagram"
        assert transliterate_indic_command("व्हाट्सएप खोलो") == "whatsapp kholo"
        assert transliterate_indic_command("यूट्यूब चलाओ") == "youtube chalao"
        assert transliterate_indic_command("गूगल खोलो") == "google kholo"
        assert transliterate_indic_command("नोटपैड खोलो") == "notepad kholo"
        assert transliterate_indic_command("गाना बजाओ") == "gaana chalao"

        # 2. Command router integration with Indic script
        r1 = CommandRouter.route_command("ओपन ए व्हाट्सएप")
        assert r1["intent"] == "open_whatsapp"

        r2 = CommandRouter.route_command("व्हाट्सएप खोलो")
        assert r2["intent"] == "open_whatsapp"

        # 3. Fallback intent parser execution with Indic script
        opened_sites = []
        opened_apps = []
        sent_messages = []
        played_yt = []
        played_spotify = []

        monkeypatch.setattr(actions, "open_website", lambda website, search_query="": opened_sites.append((website, search_query)) or {"success": True, "message": f"{website} opened"})
        monkeypatch.setattr(actions, "open_app", lambda app_name: opened_apps.append(app_name) or {"success": True, "message": f"{app_name} opened"})
        monkeypatch.setattr(brain, "dispatch_pc_tool_sync", lambda tool_name, params: opened_apps.append(params.get("app_name")) or {"success": True, "app": params.get("app_name"), "message": f"{params.get('app_name')} opened"} if tool_name == "open_app" else {"success": True})
        monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent_messages.append((contact_name, message)) or {"success": True, "message": "Sent"})
        monkeypatch.setattr(actions, "play_youtube_video", lambda query: played_yt.append(query) or {"success": True, "message": f"Playing {query}"})
        monkeypatch.setattr(actions, "play_spotify_music", lambda query="": played_spotify.append(query) or {"success": True, "message": f"Playing {query}"})

        # Test WhatsApp open
        res_wa = brain.fallback_intent_parser("ओपन ए व्हाट्सएप", session_id="test_hi_1")
        assert ("whatsapp", "") in opened_sites or any(s[0] == "whatsapp" for s in opened_sites)
        assert res_wa["action"] is not None

        # Test Instagram open
        res_ig = brain.fallback_intent_parser("ओपन इंस्टाग्राम", session_id="test_hi_2")
        assert ("instagram", "") in opened_sites or any(s[0] == "instagram" for s in opened_sites)
        assert res_ig["action"] is not None

        # Test YouTube open
        res_yt = brain.fallback_intent_parser("यूट्यूब चलाओ", session_id="test_hi_3")
        assert any(s[0] == "youtube" for s in opened_sites) or len(played_yt) > 0
        assert res_yt["action"] is not None

        # Test Google open (clean query, no "kholo" search)
        res_goog = brain.fallback_intent_parser("गूगल खोलो", session_id="test_hi_4")
        assert ("google", "") in opened_sites
        assert "Google open kar diya hai" in res_goog["reply"]

        # Test Notepad open
        res_np = brain.fallback_intent_parser("नोटपैड खोलो", session_id="test_hi_5")
        assert "notepad" in opened_apps

        # Test Spotify play
        res_sp = brain.fallback_intent_parser("गाना बजाओ", session_id="test_hi_6")
        assert len(played_spotify) > 0
        assert res_sp["action"] is not None

        # Test WhatsApp message in Devanagari
        res_msg = brain.fallback_intent_parser("व्हाट्सएप पर आरती को मैसेज भेजो हेलो", session_id="test_hi_7")
        assert len(sent_messages) == 1
        assert "आरती" in sent_messages[0][0] or "aarti" in sent_messages[0][0].lower()
        assert "hello" in sent_messages[0][1].lower()


