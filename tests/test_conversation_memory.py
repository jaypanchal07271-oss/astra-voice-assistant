"""
Automated Conversation Memory Test Suite for Astra Assistant
Verifies:
1. Multi-turn session persistence.
2. Context retention (pronouns, user details, follow-up actions).
3. Session isolation between different users/sessions.
"""

import pytest
import asyncio
from core.brain import process_voice_command, session_manager


@pytest.mark.asyncio
async def test_session_memory_remembers_user_name():
    session_id = "test_memory_session_1"
    session_manager.reset_session(session_id)

    # Turn 1: User shares a piece of information
    r1 = await process_voice_command("Mera naam Jay Panchal hai", session_id=session_id)
    assert len(r1.get("reply", "")) > 0

    # Turn 2: User asks about the information
    r2 = await process_voice_command("Mera naam kya hai?", session_id=session_id)
    if r2.get("degraded_mode"):
        assert "quota" in r2.get("reply", "").lower()
    else:
        reply = r2.get("reply", "").lower()
        assert "jay" in reply, f"Failed to remember name across turns. Reply was: {r2.get('reply')}"


@pytest.mark.asyncio
async def test_session_memory_pronoun_followup():
    session_id = "test_memory_session_2"
    session_manager.reset_session(session_id)

    # Turn 1: Open Calculator
    r1 = await process_voice_command("Calculator open karo", session_id=session_id)
    assert "calculator" in r1.get("reply", "").lower() or "calc" in r1.get("reply", "").lower()

    # Verify session manager recorded action
    last_act = session_manager.get_last_action(session_id)
    assert last_act in ["calculator", "calc"]

    # Turn 2: Follow-up pronoun 'isko band kar do'
    r2 = await process_voice_command("isko band kar do", session_id=session_id)
    reply = r2.get("reply", "").lower()
    assert "band" in reply or "calc" in reply or "calculator" in reply or "closed" in reply


@pytest.mark.asyncio
async def test_session_isolation_between_different_users():
    session_a = "user_alice"
    session_b = "user_bob"
    session_manager.reset_session(session_a)
    session_manager.reset_session(session_b)

    await process_voice_command("Mera favorite color Blue hai", session_id=session_a)
    await process_voice_command("Mera favorite color Green hai", session_id=session_b)

    res_a = await process_voice_command("Mera favorite color kya hai?", session_id=session_a)
    res_b = await process_voice_command("Mera favorite color kya hai?", session_id=session_b)

    if res_a.get("degraded_mode") or res_b.get("degraded_mode"):
        assert "quota" in res_a.get("reply", "").lower() or "quota" in res_b.get("reply", "").lower()
    else:
        assert "blue" in res_a.get("reply", "").lower()
        assert "green" in res_b.get("reply", "").lower()
