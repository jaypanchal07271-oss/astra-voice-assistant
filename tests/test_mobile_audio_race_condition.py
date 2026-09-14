"""
Tests for Mobile Voice Playback Lifecycle, Audio Unloading, and Race Condition Prevention.

Verifies:
1. Generation and session identity tracking (activePlaybackId, activeRecordingRequestId, activeRequestId).
2. Complete audio teardown and buffer purging in stopAndResetAudio(reason).
3. Safe mobile audio hardware priming using SILENT_PRIME_AUDIO (never replaying previous speech responses).
4. MediaRecorder lifecycle isolation (recording session ID matched in ondataavailable and onstop).
5. playAudioResponse stale response rejection and playback generation locking.
6. Cleanup of mobile autoplay unlock listeners to prevent tap-to-record from triggering previous speech playback.
7. Structured debug logs for voice requests and audio playback lifecycle.
"""

import re
from pathlib import Path
import pytest


@pytest.fixture
def app_js_code():
    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    assert app_js_path.exists(), "static/app.js must exist"
    return app_js_path.read_text(encoding="utf-8")


def test_session_and_playback_tracking_variables(app_js_code):
    """Verifies that tracking variables for playback generation and recording sessions exist."""
    assert "let activePlaybackId = 0;" in app_js_code
    assert "let activeRecordingRequestId = 0;" in app_js_code
    assert "let pendingPlaybackTimeout = null;" in app_js_code
    assert "let activeUnlockAudioHandler = null;" in app_js_code
    assert "SILENT_PRIME_AUDIO" in app_js_code
    assert "data:audio/wav;base64," in app_js_code


def test_stop_and_reset_audio_implementation(app_js_code):
    """Verifies stopAndResetAudio invalidates playback, clears pending timers, listeners, and purges media decoder buffer."""
    fn_match = re.search(r'function stopAndResetAudio\([^)]*\)\s*\{([\s\S]*?)\n\}\n', app_js_code)
    assert fn_match, "stopAndResetAudio function must exist"
    body = fn_match.group(1)

    # 1. Increments playback generation ID
    assert "activePlaybackId++;" in body

    # 2. Logs playback invalidation
    assert "[Audio] Playback invalidated requestId=" in body

    # 3. Clears pending playback timer
    assert "clearTimeout(pendingPlaybackTimeout);" in body

    # 4. Cleans up active unlock listener from document
    assert "removeEventListener('click', activeUnlockAudioHandler)" in body
    assert "removeEventListener('touchstart', activeUnlockAudioHandler)" in body
    assert "activeUnlockAudioHandler = null;" in body

    # 5. Removes tap-to-hear buttons
    assert ".listen-tap-btn" in body

    # 6. Nulls event handlers on audioPlayer
    assert "audioPlayer.onplay = null;" in body
    assert "audioPlayer.onended = null;" in body
    assert "audioPlayer.onpause = null;" in body
    assert "audioPlayer.onerror = null;" in body

    # 7. Purges audio buffer completely
    assert "audioPlayer.removeAttribute('src');" in body
    assert "audioPlayer.src = '';" in body
    assert "audioPlayer.load();" in body

    # 8. Resets speaking flags
    assert "isTTSPlaying = false;" in body
    assert "isSpeaking = false;" in body
    assert "updateInterruptUI(false);" in body


def test_prime_audio_playback_uses_silent_wav(app_js_code):
    """Verifies primeAudioPlayback primes audio hardware using silent WAV, never replaying old responses."""
    fn_match = re.search(r'function primeAudioPlayback\(\)\s*\{([\s\S]*?)\n\}\n', app_js_code)
    assert fn_match, "primeAudioPlayback function must exist"
    body = fn_match.group(1)

    assert "SILENT_PRIME_AUDIO" in body
    assert "audioPlayer.src = SILENT_PRIME_AUDIO;" in body
    assert "audioPlayer.play()" in body


def test_unlock_mobile_audio_uses_silent_wav(app_js_code):
    """Verifies unlockMobileAudio also uses silent audio priming to unlock mobile audio policy."""
    fn_match = re.search(r'function unlockMobileAudio\(\)\s*\{([\s\S]*?)\n\}\n', app_js_code)
    assert fn_match, "unlockMobileAudio function must exist"
    body = fn_match.group(1)

    assert "SILENT_PRIME_AUDIO" in body
    assert "audioPlayer.src = SILENT_PRIME_AUDIO;" in body


def test_is_assistant_speaking_excludes_silent_wav(app_js_code):
    """Verifies isAssistantSpeaking does not report true when playing silent priming WAV."""
    fn_match = re.search(r'function isAssistantSpeaking\(\)\s*\{([\s\S]*?)\n\}\n', app_js_code)
    assert fn_match, "isAssistantSpeaking function must exist"
    body = fn_match.group(1)

    assert "!audioPlayer.src.startsWith('data:audio/wav')" in body


def test_start_mobile_recording_lifecycle_isolation(app_js_code):
    """Verifies startMobileRecording increments requestId, calls stopAndResetAudio, primes audio, and binds session ID."""
    fn_match = re.search(r'async function startMobileRecording\(\)\s*\{([\s\S]*?)\n\}\n', app_js_code)
    assert fn_match, "startMobileRecording function must exist"
    body = fn_match.group(1)

    # Request & session ID setup
    assert "const requestId = ++activeRequestId;" in body
    assert "activeRecordingRequestId = requestId;" in body
    assert "stopAndResetAudio('start_mobile_recording');" in body
    assert "primeAudioPlayback();" in body

    # Structured logs
    assert "[VoiceRequest] Started requestId=" in body
    assert "[VoiceRequest] Recording started requestId=" in body

    # Data chunk check
    assert "activeRecordingRequestId !== requestId" in body

    # onstop validation
    assert "activeRecordingRequestId !== requestId || activeRequestId !== requestId" in body
    assert "sendVoiceUpload(audioBlob, chosenMime, requestId)" in body


def test_stop_mobile_recording_logs_requestId(app_js_code):
    """Verifies stopMobileRecording outputs structured log with activeRecordingRequestId."""
    fn_match = re.search(r'function stopMobileRecording\([^)]*\)\s*\{([\s\S]*?)(?:async function sendVoiceUpload|$)', app_js_code)
    assert fn_match, "stopMobileRecording function must exist"
    body = fn_match.group(1)

    assert "[VoiceRequest] Recording stopped requestId=" in body


def test_send_voice_upload_accepts_and_checks_requestId(app_js_code):
    """Verifies sendVoiceUpload accepts requestId, logs upload and response, and passes requestId to playAudioResponse."""
    fn_match = re.search(r'async function sendVoiceUpload\([^)]*\)\s*\{([\s\S]*?)(?:function startListening|$)', app_js_code)
    assert fn_match, "sendVoiceUpload function must exist"
    body = fn_match.group(1)

    assert "[VoiceRequest] Upload started requestId=" in body
    assert "[VoiceRequest] Response received requestId=" in body
    assert "playAudioResponse(data.audio_url, requestId);" in body


def test_play_audio_response_generation_and_stale_guards(app_js_code):
    """Verifies playAudioResponse checks activeRequestId, calls stopAndResetAudio, and tracks playbackId."""
    fn_match = re.search(r'function playAudioResponse\(([^)]*)\)\s*\{([\s\S]*?)(?:function appendAudioTapPrompt|$)', app_js_code)
    assert fn_match, "playAudioResponse function must exist"
    args = fn_match.group(1)
    body = fn_match.group(2)

    assert "audioUrl" in args
    assert "requestId" in args

    # Check stale audio rejection
    assert "[Audio] Playback requested requestId=" in body
    assert "[Audio] Current active requestId=" in body
    assert "requestId !== null && requestId !== activeRequestId" in body
    assert "[Audio] Ignoring stale audio requestId=" in body

    # Reset prior playback & increment playbackId
    assert "stopAndResetAudio('new_playback_assigned');" in body
    assert "const playbackId = activePlaybackId;" in body

    # onplay & onended check playbackId
    assert "playbackId !== activePlaybackId" in body
    assert "[Audio] Playback started requestId=" in body
    assert "[Audio] Playback ended requestId=" in body

    # Autoplay unlock handler managed
    assert "activeUnlockAudioHandler = unlockAudio;" in body
    assert "appendAudioTapPrompt(audioPlayer, requestId, playbackId);" in body


def test_append_audio_tap_prompt_checks_ids(app_js_code):
    """Verifies appendAudioTapPrompt checks playbackId and requestId before executing play()."""
    fn_match = re.search(r'function appendAudioTapPrompt\(([^)]*)\)\s*\{([\s\S]*?)(?:// 5\. User Input Event Listeners|$)', app_js_code)
    assert fn_match, "appendAudioTapPrompt function must exist"
    args = fn_match.group(1)
    body = fn_match.group(2)

    assert "requestId" in args
    assert "playbackId" in args
    assert "playbackId !== null && playbackId !== activePlaybackId" in body
    assert "requestId !== null && requestId !== activeRequestId" in body


def test_handle_voice_toggle_interrupts_when_assistant_speaking(app_js_code):
    """Verifies handleVoiceToggle interrupts audio if isAssistantSpeaking is true."""
    fn_match = re.search(r'function handleVoiceToggle[^{]*\{([\s\S]*?)(?:if\s*\(\s*micBtn\s*\)|$)', app_js_code)
    assert fn_match, "handleVoiceToggle function must exist"
    body = fn_match.group(1)

    assert "voiceState === 'speaking' || isAssistantSpeaking()" in body
    assert "interruptPlayback(false);" in body

