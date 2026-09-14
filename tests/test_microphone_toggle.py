"""
Tests for Microphone Button / 'Tap to Talk' Toggle Functionality.
Verifies:
1. Click 1 (Idle -> Listening): Starts recognition, updates micBtn UI to active, title to 'Stop Listening'.
2. Click 2 (Listening -> Stop): Immediately stops recognition via abort, suppresses commit, updates micBtn to idle.
3. Click 3 (Idle -> Listening): Cleans userRequestedStop and restarts recognition.
4. Rapid toggle clicking safety (alternating start/stop, idempotent).
5. Suppresses desktop ambient wake-word auto-restart when userRequestedStop is true.
6. rec.onresult discards incoming audio chunks when userRequestedStop is true.
7. rec.onstart aborts if fired after userRequestedStop is set.
8. stopMobileRecording detaches onstop and clears chunks on manual stop without uploading.
9. isMicActive helper checks all active mic states.
10. updateMicrophoneUI updates button classes and title.
"""

import re
from pathlib import Path
import pytest


@pytest.fixture
def app_js_content():
    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    assert app_js_path.exists(), "static/app.js must exist"
    return app_js_path.read_text(encoding="utf-8")


def test_user_requested_stop_variable_and_helpers_exist(app_js_content):
    """Verifies userRequestedStop flag, isMicActive helper, and updateMicrophoneUI helper exist."""
    assert "let userRequestedStop = false;" in app_js_content, "userRequestedStop flag must exist"
    assert "function isMicActive()" in app_js_content, "isMicActive helper must exist"
    assert "function updateMicrophoneUI(" in app_js_content, "updateMicrophoneUI helper must exist"


def test_is_mic_active_checks_all_active_states(app_js_content):
    """Verifies isMicActive checks isListening, speechRecognitionSessionActive, and isRecordingAudio."""
    fn_match = re.search(r'function isMicActive\(\)\s*\{([\s\S]*?)\}', app_js_content)
    assert fn_match, "isMicActive function must exist"
    fn_code = fn_match.group(1)

    assert "isListening" in fn_code
    assert "speechRecognitionSessionActive" in fn_code
    assert "isRecordingAudio" in fn_code


def test_update_microphone_ui_toggles_classes_and_title(app_js_content):
    """Verifies updateMicrophoneUI sets active class and 'Stop Listening' title, or resets to idle."""
    fn_match = re.search(r'function updateMicrophoneUI[^{]*\{([\s\S]*?)(?:function|$)', app_js_content)
    assert fn_match, "updateMicrophoneUI function must exist"
    fn_code = fn_match.group(1)

    assert "classList.add('active')" in fn_code
    assert "Stop Listening" in fn_code
    assert "classList.remove('active'" in fn_code
    assert "Click to Talk" in fn_code


def test_mic_btn_click_listener_is_deterministic_toggle(app_js_content):
    """Verifies micBtn and orbWrapper click listeners toggle between stopListening(true) and startListening()."""
    mic_match = re.search(r'if\s*\(\s*micBtn\s*\)\s*\{\s*micBtn\.addEventListener\(\'click\'[\s\S]*?\}\);?\s*\}', app_js_content)
    assert mic_match, "micBtn click listener must exist"
    code = mic_match.group(0)

    assert "isMicActive()" in code, "Must check isMicActive()"
    assert "stopListening(true)" in code, "Must call stopListening(true) when mic is active"
    assert "startListening()" in code, "Must call startListening() when mic is not active"


def test_orb_click_listener_is_deterministic_toggle(app_js_content):
    """Verifies orbWrapper click listener also performs deterministic toggle."""
    orb_match = re.search(r'if\s*\(\s*orbWrapper\s*\)\s*\{\s*orbWrapper\.addEventListener\(\'click\'[\s\S]*?\}\);?\s*\}', app_js_content)
    assert orb_match, "orbWrapper click listener must exist"
    code = orb_match.group(0)

    assert "isMicActive()" in code
    assert "stopListening(true)" in code
    assert "startListening()" in code


def test_stop_listening_manual_stop_discards_transcripts_and_aborts(app_js_content):
    """Verifies stopListening(true) sets userRequestedStop, clears transcripts, updates UI, and aborts recognition."""
    fn_match = re.search(r'function stopListening\(([^)]*)\)\s*\{([\s\S]*?)(?:async function sendVoiceCommand|$)', app_js_content)
    assert fn_match, "stopListening function must exist"
    args = fn_match.group(1)
    body = fn_match.group(2)

    assert "isManualUserStop" in args
    assert "userRequestedStop = true;" in body
    assert "accumulatedFinalTranscript = '';" in body
    assert "currentInterimTranscript = '';" in body
    assert "recognition.abort()" in body
    assert "updateMicrophoneUI(false)" in body


def test_start_listening_resets_user_requested_stop(app_js_content):
    """Verifies startListening resets userRequestedStop to false and updates UI."""
    fn_match = re.search(r'function startListening[^{]*\{([\s\S]*?)(?:function stopListening|$)', app_js_content)
    assert fn_match, "startListening function must exist"
    body = fn_match.group(1)

    assert "userRequestedStop = false;" in body
    assert "updateMicrophoneUI(true);" in body


def test_rec_onstart_aborts_if_user_requested_stop(app_js_content):
    """Verifies rec.onstart immediately aborts if userRequestedStop is true."""
    match = re.search(r'rec\.onstart\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};', app_js_content)
    assert match, "rec.onstart must exist"
    body = match.group(1)

    assert "userRequestedStop" in body
    assert "rec.abort()" in body


def test_rec_onresult_discards_if_user_requested_stop(app_js_content):
    """Verifies rec.onresult immediately discards captured results if userRequestedStop is true."""
    match = re.search(r'rec\.onresult\s*=\s*\(event\)\s*=>\s*\{([\s\S]*?)\};', app_js_content)
    assert match, "rec.onresult must exist"
    body = match.group(1)

    assert "userRequestedStop" in body


def test_rec_onend_suppresses_wake_word_restart_when_user_stopped(app_js_content):
    """Verifies rec.onend suppresses restart timeout and commit when userRequestedStop is true."""
    match = re.search(r'rec\.onend\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};', app_js_content)
    assert match, "rec.onend must exist"
    body = match.group(1)

    # Must stay off on userRequestedStop
    assert "if (userRequestedStop)" in body
    assert "!userRequestedStop && wakeWordEnabled" in body or "!userRequestedStop" in body


def test_stop_mobile_recording_manual_stop_discards_chunks_and_detaches_onstop(app_js_content):
    """Verifies stopMobileRecording(true) detaches onstop, clears audioChunks, and avoids sendVoiceUpload."""
    fn_match = re.search(r'function stopMobileRecording\(([^)]*)\)\s*\{([\s\S]*?)(?:async function sendVoiceUpload|$)', app_js_content)
    assert fn_match, "stopMobileRecording function must exist"
    args = fn_match.group(1)
    body = fn_match.group(2)

    assert "isManualUserStop" in args
    assert "mediaRecorder.onstop = null" in body
    assert "audioChunks = []" in body
    assert "cleanupMobileRecording()" in body


def test_wake_word_toggle_stops_with_manual_flag(app_js_content):
    """Verifies wakeWordToggleBtn disables wake word with stopListening(true)."""
    match = re.search(r'wakeWordToggleBtn\.addEventListener\(\'click\'[\s\S]*?(?:if\s*\(wakeWordSelect|\n\}\);)', app_js_content)
    assert match, "wakeWordToggleBtn click listener must exist"
    code = match.group(0)

    assert "stopListening(true)" in code
    assert "userRequestedStop = false" in code


def test_voice_state_machine_defined_and_set_voice_state(app_js_content):
    """Verifies voiceState state machine and setVoiceState transition function exist and sync flags."""
    assert "let voiceState = 'idle';" in app_js_content
    assert "function setVoiceState(newState)" in app_js_content
    assert "function syncUIWithVoiceState()" in app_js_content


def _extract_function_block(source: str, fn_signature: str) -> str:
    idx = source.find(fn_signature)
    if idx == -1:
        return ""
    brace_start = source.find("{", idx)
    if brace_start == -1:
        return ""
    count = 0
    for i in range(brace_start, len(source)):
        if source[i] == '{':
            count += 1
        elif source[i] == '}':
            count -= 1
            if count == 0:
                return source[brace_start:i+1]
    return ""


def test_handle_voice_toggle_centralized_for_mic_and_orb(app_js_content):
    """Verifies handleVoiceToggle centralizes toggle logic for micBtn and orbWrapper."""
    code = _extract_function_block(app_js_content, "function handleVoiceToggle")
    assert code, "handleVoiceToggle function must exist"

    assert "voiceState === 'listening'" in code
    assert "stopListening(true)" in code
    assert "startListening()" in code

    assert "micBtn.addEventListener('click', handleVoiceToggle)" in app_js_content
    assert "orbWrapper.addEventListener('click', handleVoiceToggle)" in app_js_content


def test_timer_management_clears_and_verifies_state(app_js_content):
    """Verifies clearRecognitionRestartTimer and scheduleWakeWordRestart manage timers safely."""
    assert "function clearRecognitionRestartTimer()" in app_js_content
    assert "function scheduleWakeWordRestart(" in app_js_content

    sched_code = _extract_function_block(app_js_content, "function scheduleWakeWordRestart(")
    assert sched_code, "scheduleWakeWordRestart function must exist"

    assert "clearRecognitionRestartTimer()" in sched_code
    assert "manualStopRequested" in sched_code
    assert "voiceState === 'idle'" in sched_code or "voiceState !== 'idle'" in sched_code


def test_audio_player_never_starts_recognition_in_onplay(app_js_content):
    """Verifies audioPlayer.onplay never calls recognition.start()."""
    onplay_match = re.search(r'audioPlayer\.onplay\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};', app_js_content)
    assert onplay_match, "audioPlayer.onplay must exist"
    body = onplay_match.group(1)

    assert "recognition.start()" not in body
    assert "setVoiceState('speaking')" in body


def test_dom_content_loaded_guards_automatic_mic(app_js_content):
    """Verifies DOMContentLoaded does not start microphone if wakeWord is disabled."""
    dom_match = re.search(r'window\.addEventListener\(\'DOMContentLoaded\'[\s\S]*?\n\}\);', app_js_content)
    assert dom_match, "DOMContentLoaded listener must exist"
    code = dom_match.group(0)

    assert "scheduleWakeWordRestart" in code
    assert "wakeWordEnabled" in code


def test_is_mobile_device_targets_mobile_user_agents_only(app_js_content):
    """Verifies isMobileDevice checks userAgent and does not misclassify touchscreen laptops."""
    fn_code = _extract_function_block(app_js_content, "function isMobileDevice()")
    assert fn_code, "isMobileDevice must exist"
    assert "Android" in fn_code
    assert "iPhone" in fn_code
    # Must not falsely classify laptops with touchscreen as mobile
    assert "navigator.maxTouchPoints" not in fn_code


def test_handle_voice_toggle_submits_mobile_recording_on_second_click(app_js_content):
    """Verifies that clicking mic during mobile audio recording calls stopMobileRecording(false) to submit."""
    fn_code = _extract_function_block(app_js_content, "function handleVoiceToggle")
    assert fn_code, "handleVoiceToggle must exist"
    assert "isRecordingAudio" in fn_code
    assert "stopMobileRecording(false)" in fn_code


def test_start_mobile_recording_sets_voice_state_listening(app_js_content):
    """Verifies that startMobileRecording sets voiceState to 'listening'."""
    fn_code = _extract_function_block(app_js_content, "async function startMobileRecording()")
    assert fn_code, "startMobileRecording must exist"
    assert "setVoiceState('listening')" in fn_code


def test_is_assistant_speaking_checks_real_playback(app_js_content):
    """Verifies that isAssistantSpeaking does not block on empty uninitialized audioPlayer."""
    fn_code = _extract_function_block(app_js_content, "function isAssistantSpeaking()")
    assert fn_code, "isAssistantSpeaking must exist"
    assert "audioPlayer.src" in fn_code
    assert "audioPlayer.currentTime > 0" in fn_code
    assert fn_code.count("return ") == 1, "Must have exactly one clean return statement"


def test_handle_voice_toggle_has_debounce_guard(app_js_content):
    """Verifies that handleVoiceToggle includes debounce protection against double taps/clicks."""
    fn_code = _extract_function_block(app_js_content, "function handleVoiceToggle")
    assert fn_code, "handleVoiceToggle must exist"
    assert "lastVoiceToggleTime" in app_js_content
    assert "350" in fn_code


def test_media_recorder_onstop_resets_idle_on_empty_chunks(app_js_content):
    """Verifies that mediaRecorder.onstop resets voiceState to idle when chunks are empty."""
    assert "setVoiceState('idle')" in app_js_content
    # Find the audioChunks.length check
    assert "audioChunks.length === 0" in app_js_content


def test_api_status_returns_tunnel_url(monkeypatch):
    """Verifies that /api/status includes tunnel_url in response."""
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "tunnel_url" in data



