import re
from pathlib import Path
import pytest

@pytest.fixture
def app_js_content():
    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    assert app_js_path.exists(), "static/app.js must exist"
    return app_js_path.read_text(encoding="utf-8")

def test_commit_and_submit_transcript_early_lock_and_deduplication(app_js_content):
    """Verifies commitAndSubmitTranscript blocks when processing/speaking, deduplicates within 2500ms, and locks isProcessing early."""
    fn_match = re.search(r'function commitAndSubmitTranscript[^{]*\{([\s\S]*?)(?:function|$)', app_js_content)
    assert fn_match, "commitAndSubmitTranscript function must exist"
    fn_code = fn_match.group(1)

    # 1. Guards against submission while assistant is speaking or already processing
    assert "if (isProcessing || isAssistantSpeaking())" in fn_code

    # 2. Deduplication check against submittedTranscript
    assert "submittedTranscript === cleanCommand" in fn_code
    assert "lastSubmissionTime" in fn_code

    # 3. Sets isProcessing = true BEFORE stopListening() and sendVoiceCommand()
    pos_processing = fn_code.find("isProcessing = true;")
    pos_stop = fn_code.find("stopListening();")
    pos_send = fn_code.find("sendVoiceCommand(cleanCommand);")
    assert pos_processing != -1, "isProcessing must be set to true"
    assert pos_stop != -1, "stopListening() must be called"
    assert pos_send != -1, "sendVoiceCommand() must be called"
    assert pos_processing < pos_stop, "isProcessing must be set before stopListening()"
    assert pos_processing < pos_send, "isProcessing must be set before sendVoiceCommand()"

def test_send_voice_command_concurrency_guard_and_early_lock(app_js_content):
    """Verifies sendVoiceCommand blocks concurrent conflicting requests and locks state early."""
    fn_match = re.search(r'async function sendVoiceCommand[^{]*\{([\s\S]*?)(?:function\s+updateInterruptUI|$)', app_js_content)
    assert fn_match, "sendVoiceCommand function must exist"
    fn_code = fn_match.group(1)

    # 1. Guard against concurrent execution
    assert "if (isProcessing && currentlyProcessingTranscript && currentlyProcessingTranscript !== cleanCommand)" in fn_code

    # 2. Early state activation
    pos_lock = fn_code.find("isProcessing = true;")
    pos_abort = fn_code.find("recognition.abort()")
    pos_fetch = fn_code.find("fetch('/api/chat'")
    assert pos_lock != -1
    assert pos_abort != -1
    assert pos_fetch != -1
    assert pos_lock < pos_abort, "isProcessing must be set before recognition.abort()"
    assert pos_lock < pos_fetch, "isProcessing must be set before fetch"

    # 3. Request completion cleanups
    assert "isProcessing = false;" in fn_code
    assert "currentlyProcessingTranscript = '';" in fn_code

def test_rec_onresult_discards_while_processing_or_speaking(app_js_content):
    """Verifies rec.onresult discards recognition results when isProcessing is active."""
    onresult_match = re.search(r'rec\.onresult\s*=\s*\(event\)\s*=>\s*\{([\s\S]*?)\};', app_js_content)
    assert onresult_match, "rec.onresult handler must exist"
    body = onresult_match.group(1)

    # Must discard if isProcessing or assistant speaking
    assert "isProcessing" in body
    assert "isAssistantSpeaking()" in body
    assert "if (isTTSPlaying || isSpeaking || isAssistantSpeaking() || isProcessing)" in body

def test_rec_onend_blocks_restart_and_commit_while_processing(app_js_content):
    """Verifies rec.onend does not commit or restart while processing."""
    onend_match = re.search(r'rec\.onend\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};', app_js_content)
    assert onend_match, "rec.onend handler must exist"
    body = onend_match.group(1)

    assert "if (isTTSPlaying || isSpeaking || isAssistantSpeaking() || isProcessing)" in body

def test_start_listening_restart_safety_and_invalid_state_handling(app_js_content):
    """Verifies startListening does not double-start or blindly fallback to MediaRecorder on InvalidStateError."""
    fn_match = re.search(r'function startListening[^{]*\{([\s\S]*?)(?:function stopListening|$)', app_js_content)
    assert fn_match, "startListening function must exist"
    fn_code = fn_match.group(1)

    # 1. Guards against redundant active session
    assert "if (isListening && speechRecognitionSessionActive)" in fn_code

    # 2. Does not wipe deduplication submittedTranscript memory
    assert "submittedTranscript = '';" not in fn_code

    # 3. Graceful InvalidStateError handling
    assert "InvalidStateError" in fn_code

def test_handle_text_command_submit_guards_processing(app_js_content):
    """Verifies handleTextCommandSubmit blocks when isProcessing is active."""
    fn_match = re.search(r'function handleTextCommandSubmit[^{]*\{([\s\S]*?)\}', app_js_content)
    assert fn_match, "handleTextCommandSubmit function must exist"
    fn_code = fn_match.group(1)

    assert "if (isProcessing)" in fn_code

def test_interrupt_playback_clears_processing_and_transcript(app_js_content):
    """Verifies interruptPlayback resets isProcessing and currentlyProcessingTranscript."""
    fn_match = re.search(r'function interruptPlayback[^{]*\{([\s\S]*?)(?:function playAudioResponse|$)', app_js_content)
    assert fn_match, "interruptPlayback function must exist"
    fn_code = fn_match.group(1)

    assert "isProcessing = false;" in fn_code
    assert "currentlyProcessingTranscript = '';" in fn_code
