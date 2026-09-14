// ASTRA 2.0 - AI Voice Assistant: PWA Shell, Frontend Mic, Wake Word, Memory & Audio Stream Controller

// Register PWA Service Worker for offline shell caching
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js')
            .then((reg) => console.log('[Astra PWA] Service Worker registered with scope:', reg.scope))
            .catch((err) => console.warn('[Astra PWA] Service Worker registration failed:', err));
    });
}

// UI Elements
const statusDot = document.querySelector('.status-dot');
const statusText = document.getElementById('statusText');
const orbWrapper = document.getElementById('orbWrapper');
const liveTranscript = document.getElementById('liveTranscript');
const assistantReply = document.getElementById('assistantReply');
const micBtn = document.getElementById('micBtn');
const audioPlayer = document.getElementById('audioPlayer') || new Audio();
const langBtns = document.querySelectorAll('.lang-btn');
const wakeWordToggleBtn = document.getElementById('wakeWordToggleBtn');
const wakeWordText = document.getElementById('wakeWordText');
const pushNotificationBtn = document.getElementById('pushNotificationBtn');
const pushNotificationText = document.getElementById('pushNotificationText');
const textCommandForm = document.getElementById('textCommandForm');
const textCommandInput = document.getElementById('textCommandInput');
const sendTextBtn = document.getElementById('sendTextBtn');
const chatMessages = document.getElementById('chatMessages');
const liveTranscriptWrapper = document.getElementById('liveTranscriptWrapper');

console.log('[VoiceUI] micBtn found:', !!micBtn);
console.log('[VoiceUI] orbWrapper found:', !!orbWrapper);

// Executive Console Sidebar & Drawer Navigation Elements
const appSidebar = document.getElementById('appSidebar');
const mobileDrawerBtn = document.getElementById('mobileDrawerBtn');
const sidebarCloseBtn = document.getElementById('sidebarCloseBtn');
const sidebarCollapseBtn = document.getElementById('sidebarCollapseBtn');
const sidebarBackdrop = document.getElementById('sidebarBackdrop');
const newChatBtn = document.getElementById('newChatBtn');
const sidebarSettingsBtn = document.getElementById('sidebarSettingsBtn');
const omnibarToolsBtn = document.getElementById('omnibarToolsBtn');
const welcomeHero = document.getElementById('welcomeHero');
const promptDeck = document.getElementById('promptDeck');

// Settings Modal
const settingsBtn = document.getElementById('settingsBtn');
const settingsModal = document.getElementById('settingsModal');
const closeModalBtn = document.getElementById('closeModalBtn');
const saveKeyBtn = document.getElementById('saveKeyBtn');
const apiKeyInput = document.getElementById('apiKeyInput');
const saveStatus = document.getElementById('saveStatus');
const wakeWordSelect = document.getElementById('wakeWordSelect');
const hintWakeWord = document.getElementById('hintWakeWord');

// Security Token Extraction (Session Cookie, Meta Tag & Persistent LocalStorage)
const metaToken = document.querySelector('meta[name="astra-token"]');
let astraToken = metaToken ? metaToken.getAttribute('content') : "";
if (astraToken) {
    localStorage.setItem('astra_token', astraToken);
} else {
    astraToken = localStorage.getItem('astra_token') || "";
}

// Active Cloudflare HTTPS Tunnel URL for seamless mobile mic access
let cachedTunnelUrl = "";

// Device Detection Helper
function isMobileDevice() {
    return /Android|iPhone|iPad|iPod|webOS|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
}

// State Variables & Voice State Machine (idle | listening | stopping | processing | speaking)
let voiceState = 'idle';
let manualStopRequested = false;
let userRequestedStop = false;          // Synchronized mirror flag for manualStopRequested
let recognitionRestartTimer = null;     // Single managed ambient restart timer
let wakeWordRestartTimeout = null;      // Track ambient restart timeout to cancel during TTS

let isTTSPlaying = false; // Primary flag: True from TTS preparation to completion
let isSpeaking = false;   // Mirror flag for external checks and compatibility
let isListening = false;
let currentLang = 'hi-IN'; // default to Hindi/Hinglish (hi-IN)
let recognition = null;
let lastProcessedTranscript = "";
let isProcessing = false;
let wakeWordEnabled = (localStorage.getItem('astra_wake_word_enabled') === 'true'); // Default OFF unless explicitly enabled
let wakeWordActive = false;

// Request ID & Timeout Safety Management (Prevents Stuck State & Stale Late Responses)
let activeRequestId = 0;
let activeChatAbortController = null;
let activeVoiceUploadAbortController = null;
const FETCH_TIMEOUT_MS = 45000; // 45 seconds safe network timeout

// Playback generation & session tracking to prevent race conditions and stale audio playback
let activePlaybackId = 0;
let activeRecordingRequestId = 0;
let pendingPlaybackTimeout = null;
let activeUnlockAudioHandler = null;
// 1ms silent WAV data URI to safely prime mobile audio hardware without replaying previous speech
const SILENT_PRIME_AUDIO = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";


// Voice State Controller: Single source of truth for the entire voice lifecycle
function setVoiceState(newState) {
    if (voiceState === newState) return;
    console.log(`[VoiceState] ${voiceState} -> ${newState}`);
    voiceState = newState;

    // Synchronize boolean mirror flags for backward compatibility
    isListening = (newState === 'listening');
    speechRecognitionSessionActive = (newState === 'listening');
    isProcessing = (newState === 'processing');
    isSpeaking = (newState === 'speaking');
    isTTSPlaying = (newState === 'speaking');
    if (newState !== 'listening') {
        isRecordingAudio = false;
    }

    // Synchronize UI elements
    syncUIWithVoiceState();
}

// Synchronize UI from current voiceState
function syncUIWithVoiceState() {
    switch (voiceState) {
        case 'idle':
            updateMicrophoneUI(false);
            setOrbState('idle');
            updateInterruptUI(false);
            if (statusText) statusText.textContent = "Tap to speak";
            break;
        case 'listening':
            updateMicrophoneUI(true);
            setOrbState('listening');
            updateInterruptUI(false);
            if (statusText) statusText.textContent = "Listening...";
            break;
        case 'stopping':
            updateMicrophoneUI(false);
            updateInterruptUI(false);
            break;
        case 'processing':
            updateMicrophoneUI(false);
            setOrbState('thinking');
            updateInterruptUI(false);
            if (statusText) statusText.textContent = "Thinking...";
            break;
        case 'speaking':
            updateMicrophoneUI(false);
            setOrbState('speaking');
            updateInterruptUI(true);
            if (statusText) statusText.textContent = "Speaking...";
            break;
        case 'error':
            updateMicrophoneUI(false);
            setOrbState('error');
            updateInterruptUI(false);
            if (statusText) statusText.textContent = "Something went wrong";
            break;
    }
}

// Helper to check if assistant audio is currently playing
function isAssistantSpeaking() {
    const isAudioActuallyPlaying = Boolean(
        audioPlayer &&
        audioPlayer.src &&
        audioPlayer.src.trim() !== '' &&
        !audioPlayer.src.startsWith('data:audio/wav') &&
        !audioPlayer.paused &&
        !audioPlayer.ended &&
        audioPlayer.currentTime > 0
    );
    return isTTSPlaying || isSpeaking || (voiceState === 'speaking') || isAudioActuallyPlaying;
}

// Helper to check if microphone / audio recording is active
function isMicActive() {
    return (voiceState === 'listening') || Boolean(isListening || speechRecognitionSessionActive || isRecordingAudio);
}

// Helper to update microphone button UI state and title
function updateMicrophoneUI(active) {
    if (micBtn) {
        if (active) {
            micBtn.classList.add('active');
            micBtn.title = "Stop Listening";
        } else {
            micBtn.classList.remove('active', 'recording');
            micBtn.title = "Click to Talk (or press Spacebar)";
        }
    }
}

// Timer manager for ambient wake word restarts
function clearRecognitionRestartTimer() {
    if (recognitionRestartTimer) {
        clearTimeout(recognitionRestartTimer);
        recognitionRestartTimer = null;
    }
    if (wakeWordRestartTimeout) {
        clearTimeout(wakeWordRestartTimeout);
        wakeWordRestartTimeout = null;
    }
}

function scheduleWakeWordRestart(delayMs = 300) {
    clearRecognitionRestartTimer();
    if (!wakeWordEnabled || isMobileDevice() || manualStopRequested || userRequestedStop || manualVoiceSession) {
        return;
    }
    if (voiceState !== 'idle') {
        return;
    }

    recognitionRestartTimer = setTimeout(() => {
        recognitionRestartTimer = null;
        wakeWordRestartTimeout = null;
        if (wakeWordEnabled && !isMobileDevice() && !manualStopRequested && !userRequestedStop && !manualVoiceSession && voiceState === 'idle' && !isProcessing && !isTTSPlaying && !isSpeaking && !isAssistantSpeaking()) {
            console.log("[Mic] Restarting ambient wake-word listening...");
            startListening();
        }
    }, delayMs);
    wakeWordRestartTimeout = recognitionRestartTimer;
}

// Mobile Audio Autoplay Policy Unlocker (Unlocks HTML5 audio on first touch/tap)
let audioUnlocked = false;
function unlockMobileAudio() {
    if (audioUnlocked) return;
    if (isAssistantSpeaking()) return;
    if (audioPlayer) {
        const prevSrc = audioPlayer.src;
        if (!prevSrc || prevSrc.startsWith('data:audio/wav')) {
            audioPlayer.src = SILENT_PRIME_AUDIO;
            audioPlayer.play().then(() => {
                audioPlayer.pause();
                audioPlayer.currentTime = 0;
                audioUnlocked = true;
                console.log("[Audio] Mobile audio unlocked for playback.");
            }).catch(() => {});
        }
    }
}
window.addEventListener('touchstart', unlockMobileAudio, { passive: true });
window.addEventListener('click', unlockMobileAudio, { passive: true });

// Persistent Session ID for Conversation Memory
let astraSessionId = sessionStorage.getItem('astra_session_id');
if (!astraSessionId) {
    astraSessionId = 'sess_' + Math.random().toString(36).substring(2, 10);
    sessionStorage.setItem('astra_session_id', astraSessionId);
}

// Wake Word Trigger Preference ('both', 'astra', or 'jarvis')
let wakeWordPreference = localStorage.getItem('astra_wake_word_preference') || 'both';

function getWakeWordRegex() {
    if (wakeWordPreference === 'jarvis') {
        return /\b(?:hey\s+jarvis|suno\s+jarvis|hello\s+jarvis|jarvis)\b/i;
    } else if (wakeWordPreference === 'astra') {
        return /\b(?:hey\s+astra|suno\s+astra|hello\s+astra|astra)\b/i;
    } else {
        return /\b(?:hey\s+astra|suno\s+astra|hello\s+astra|astra|hey\s+jarvis|suno\s+jarvis|hello\s+jarvis|jarvis)\b/i;
    }
}

function getWakeWordStripRegex() {
    if (wakeWordPreference === 'jarvis') {
        return /\b(?:hey\s+jarvis|suno\s+jarvis|hello\s+jarvis|jarvis)\b/gi;
    } else if (wakeWordPreference === 'astra') {
        return /\b(?:hey\s+astra|suno\s+astra|hello\s+astra|astra)\b/gi;
    } else {
        return /\b(?:hey\s+astra|suno\s+astra|hello\s+astra|astra|hey\s+jarvis|suno\s+jarvis|hello\s+jarvis|jarvis)\b/gi;
    }
}

function getWakeWordDisplayName() {
    if (wakeWordPreference === 'jarvis') return 'Jarvis';
    if (wakeWordPreference === 'astra') return 'Astra';
    return 'Astra / Jarvis';
}

function updateWakeWordUI() {
    const name = getWakeWordDisplayName();
    if (wakeWordText) {
        wakeWordText.textContent = wakeWordEnabled ? `Wake Word: ON` : `Wake Word: OFF`;
    }
    if (wakeWordToggleBtn) {
        wakeWordToggleBtn.title = `Toggle Hands-Free Wake Word ('Hey ${name}')`;
    }
    if (hintWakeWord) {
        if (wakeWordPreference === 'both') {
            hintWakeWord.textContent = `"Hey Astra" / "Hey Jarvis"`;
        } else if (wakeWordPreference === 'jarvis') {
            hintWakeWord.textContent = `"Hey Jarvis"`;
        } else {
            hintWakeWord.textContent = `"Hey Astra"`;
        }
    }
    if (wakeWordSelect) {
        wakeWordSelect.value = wakeWordPreference;
    }
}

// Robust Mobile MediaRecorder State Machine & Timer Variables
let mediaRecorder = null;
let audioChunks = [];
let isRecordingAudio = false;
let recordingStream = null;
let recordingTimerInterval = null;
let recordingSeconds = 0;
let recordingSafetyTimeout = null;
// Recognition Modes & Multi-Turn Mobile Session Architecture
const RECOGNITION_MODE = {
    NONE: 'NONE',
    MANUAL: 'MANUAL',
    AMBIENT_WAKEWORD: 'AMBIENT_WAKEWORD'
};
let recognitionMode = RECOGNITION_MODE.NONE;
let recognitionSessionId = 0;             // Monotonically increasing ID for SpeechRecognition sessions
let commandSubmittedForSession = false;   // Guard against duplicate command submission per session

// Speech Recognition Explicit Lifecycle & Transcript State
let accumulatedFinalTranscript = '';      // All finalized speech segments
let currentInterimTranscript = '';        // Current in-progress speech (unfinalized)
let submittedTranscript = '';             // Last successfully submitted transcript
let currentlyProcessingTranscript = '';   // Transcript currently in-flight to backend
let lastSubmissionTime = 0;               // Timestamp of last submission to prevent rapid duplicates
let speechSilenceTimeout = null;          // Silence debounce timer after speech finalization
let speechRecognitionSessionActive = false;
let manualVoiceSession = false;           // Distinguishes explicit user mic clicks from ambient wake word
let voiceSessionId = 0;                  // Monotonically increasing ID for MediaRecorder sessions

function normalizeTranscript(text) {
    return (text || '')
        .toLowerCase()
        .replace(/[.,!?;:]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function mergeTranscripts(existing, incoming) {
    if (!existing || !existing.trim()) return (incoming || '').trim();
    if (!incoming || !incoming.trim()) return existing.trim();

    const normExist = normalizeTranscript(existing);
    const normInc = normalizeTranscript(incoming);

    if (normExist === normInc) return existing.trim();
    if (normInc.startsWith(normExist)) return incoming.trim();
    if (normExist.startsWith(normInc)) return existing.trim();
    if (normExist.endsWith(normInc)) return existing.trim();

    // If incoming multi-word segment is entirely contained in existing transcript, discard duplicate
    const incWordsCount = normInc.split(/\s+/).length;
    if (incWordsCount >= 2 && normExist.includes(normInc)) {
        return existing.trim();
    }
    // If existing transcript is entirely contained inside incoming, take the fuller incoming transcript
    const existWordsCount = normExist.split(/\s+/).length;
    if (existWordsCount >= 2 && normInc.includes(normExist)) {
        return incoming.trim();
    }

    const eWords = existing.trim().split(/\s+/);
    const iWords = incoming.trim().split(/\s+/);
    const maxOverlap = Math.min(eWords.length, iWords.length);

    let bestK = 0;
    for (let k = maxOverlap; k >= 1; k--) {
        const eSlice = normalizeTranscript(eWords.slice(-k).join(' '));
        const iSlice = normalizeTranscript(iWords.slice(0, k).join(' '));
        if (eSlice === iSlice) {
            bestK = k;
            break;
        }
    }

    if (bestK > 0) {
        const remaining = iWords.slice(bestK).join(' ');
        return remaining ? (existing.trim() + ' ' + remaining).trim() : existing.trim();
    }

    return (existing.trim() + ' ' + incoming.trim()).trim();
}

if (typeof window !== 'undefined') {
    window.mergeTranscripts = mergeTranscripts;
    window.normalizeTranscript = normalizeTranscript;
}

function commitAndSubmitTranscript(rawText, source = "unknown") {
    if (speechSilenceTimeout) {
        clearTimeout(speechSilenceTimeout);
        speechSilenceTimeout = null;
    }

    if (!rawText || !rawText.trim()) {
        console.warn(`[Mic] commitAndSubmitTranscript (${source}): Discarding empty transcript.`);
        return;
    }

    let command = rawText.trim();

    // Strip wake word if present
    if (wakeWordEnabled) {
        const stripRegex = getWakeWordStripRegex();
        const stripped = command.replace(stripRegex, '').trim();
        if (stripped) {
            command = stripped;
        } else if (!wakeWordActive) {
            console.log(`[Mic] User spoke wake word only. Waiting for user command.`);
            wakeWordActive = true;
            if (statusText) statusText.textContent = "Astra active! Kahiye kya command hai?";
            return;
        }
    }

    // Guard against submission while assistant is speaking or already processing
    if (isProcessing || isAssistantSpeaking()) {
        console.warn(`[Mic] commitAndSubmitTranscript (${source}): Blocked because assistant is already processing or speaking.`);
        return;
    }

    // Guard against empty or whitespace-only command
    if (!command || !command.trim()) {
        console.warn(`[Mic] commitAndSubmitTranscript (${source}): Command became empty after trimming.`);
        return;
    }

    const cleanCommand = command.trim();

    // Deduplication check: ignore identical command within 2500ms
    const now = Date.now();
    if (submittedTranscript === cleanCommand && (now - lastSubmissionTime < 2500)) {
        console.warn(`[Mic] commitAndSubmitTranscript (${source}): Suppressing duplicate recent submission: "${cleanCommand}"`);
        return;
    }

    console.log(`[Mic] Submitting transcript (${source}): "${cleanCommand}"`);
    console.log(`[Mic] (Full accumulated final text was: "${accumulatedFinalTranscript}")`);

    if (commandSubmittedForSession) {
        console.warn(`[Mic] commitAndSubmitTranscript (${source}): Duplicate submission for session ${recognitionSessionId} ignored.`);
        return;
    }
    commandSubmittedForSession = true;
    recognitionMode = RECOGNITION_MODE.NONE;
    console.log('[STT] Command submitted: session=' + recognitionSessionId);

    // Lock processing state early before stopping listeners or calling backend
    isProcessing = true;
    setVoiceState('processing');
    submittedTranscript = cleanCommand;
    currentlyProcessingTranscript = cleanCommand;
    lastSubmissionTime = now;

    // Clear accumulated transcript buffers for the next cycle
    accumulatedFinalTranscript = '';
    currentInterimTranscript = '';
    wakeWordActive = false;

    clearRecognitionRestartTimer();

    stopListening();

    if (liveTranscript) liveTranscript.textContent = '';
    if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');

    sendVoiceCommand(cleanCommand);
}

// 1. Initialize Web Speech API
function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        if (statusText) {
            if (window.location.protocol !== 'https:' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
                statusText.textContent = "⚠️ Mobile mic requires HTTPS. Connect via https://" + window.location.host;
            } else {
                statusText.textContent = "Astra Ready • Click Mic to Record & Talk";
            }
        }
        console.log("[Mic] SpeechRecognition not available; MediaRecorder fallback active.");
        return null;
    }

    const rec = new SpeechRecognition();
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.lang = currentLang;

    rec.onstart = () => {
        if (manualStopRequested || userRequestedStop) {
            console.log("[Mic] Speech recognition started after user requested stop; aborting immediately.");
            try { rec.abort(); } catch (e) {}
            speechRecognitionSessionActive = false;
            isListening = false;
            setVoiceState('idle');
            return;
        }

        if (isTTSPlaying || isSpeaking || isAssistantSpeaking() || voiceState === 'speaking' || voiceState === 'processing') {
            console.warn("[Mic] Speech recognition started while assistant is speaking! Aborting immediately.");
            try { rec.abort(); } catch (e) {}
            return;
        }
        speechRecognitionSessionActive = true;
        isListening = true;
        setVoiceState('listening');
        currentInterimTranscript = '';
        if (statusText) statusText.textContent = "Listening...";
        console.log("[Mic] Speech recognition started. Language:", rec.lang);
    };

    rec.onspeechstart = () => {
        console.log("[Mic] Speech sound detected (onspeechstart).");
        if (speechSilenceTimeout) {
            clearTimeout(speechSilenceTimeout);
            speechSilenceTimeout = null;
        }

        // Prevent loopback: if assistant is speaking or playing TTS, abort recognition so mic doesn't capture assistant's own voice
        if (isTTSPlaying || isSpeaking || isAssistantSpeaking()) {
            console.log("[Astra] Sound detected during assistant speech; aborting recognition.");
            try { rec.abort(); } catch (e) {}
            return;
        }
    };

    rec.onspeechend = () => {
        console.log("[Mic] Speech sound paused (onspeechend).");
    };

    rec.onresult = (event) => {
        // Discard any audio frames if user manually stopped listening
        if (userRequestedStop) {
            console.log("[Mic] Discarding recognition result after manual user stop.");
            return;
        }

        // Discard any audio frames captured while assistant is speaking or processing
        if (isTTSPlaying || isSpeaking || isAssistantSpeaking() || isProcessing) {
            console.log("[Mic] Discarding recognition result while assistant is speaking or processing.");
            return;
        }

        const activeSession = recognitionSessionId;
        console.log('[STT] Result received: session=' + activeSession + ' resultIndex=' + event.resultIndex);

        let sessionFinal = '';
        let sessionInterim = '';

        for (let i = 0; i < event.results.length; ++i) {
            const item = event.results[i];
            const transcript = item[0].transcript;
            if (item.isFinal) {
                sessionFinal = mergeTranscripts(sessionFinal, transcript);
            } else {
                sessionInterim = mergeTranscripts(sessionInterim, transcript);
            }
        }

        if (sessionFinal) {
            const prevFinal = accumulatedFinalTranscript;
            accumulatedFinalTranscript = mergeTranscripts(accumulatedFinalTranscript, sessionFinal);
            if (accumulatedFinalTranscript !== prevFinal) {
                console.log("[STT] Final segment:", accumulatedFinalTranscript);
            } else {
                console.log("[STT] Ignoring duplicate segment:", sessionFinal);
            }
        }
        currentInterimTranscript = sessionInterim.trim();
        if (currentInterimTranscript) {
            console.log("[STT] Interim:", currentInterimTranscript);
        }

        console.log("[Mic] onresult -> accumulatedFinal:", accumulatedFinalTranscript || "(none)", "| currentInterim:", currentInterimTranscript || "(none)");
        const fullDisplayText = mergeTranscripts(accumulatedFinalTranscript, currentInterimTranscript);

        // 1. Ambient Wake Word Detection ("Hey Astra" / "Hey Jarvis" / "Astra" / "Jarvis")
        if (wakeWordEnabled && !isProcessing && recognitionMode === RECOGNITION_MODE.AMBIENT_WAKEWORD) {
            const wakeRegex = getWakeWordRegex();
            if (wakeRegex.test(fullDisplayText)) {
                const cleanedCommand = fullDisplayText.replace(wakeRegex, '').replace(/^[,.:\s-]+/, '').trim();
                const matchedName = getWakeWordDisplayName();

                if (orbWrapper) {
                    orbWrapper.classList.add('listening');
                }

                if (cleanedCommand.length > 2 && accumulatedFinalTranscript) {
                    console.log(`[${matchedName}] Wake word with command in progress:`, cleanedCommand);
                } else if (!wakeWordActive) {
                    wakeWordActive = true;
                    console.log(`[${matchedName}] Wake word triggered! Waiting for speech.`);
                    if (statusText) statusText.textContent = `${matchedName} active! Kahiye kya command hai?`;
                    if (liveTranscript) liveTranscript.textContent = `✦ ${matchedName} Listening...`;
                }
            }
        }

        if (liveTranscript) {
            liveTranscript.textContent = fullDisplayText;
        }
        if (liveTranscriptWrapper) {
            if (fullDisplayText.length > 0) {
                liveTranscriptWrapper.classList.add('active');
            } else {
                liveTranscriptWrapper.classList.remove('active');
            }
        }

        // 2. Silence Debounce & Command Submission:
        // If there is active interim speech, the user is still speaking! Cancel pending submission.
        // In MANUAL mode, the user controls when to stop by tapping the button.
        // DO NOT prematurely submit after a short 1-second pause while the user is still speaking!
        if (recognitionMode === RECOGNITION_MODE.MANUAL) {
            if (speechSilenceTimeout) {
                clearTimeout(speechSilenceTimeout);
                speechSilenceTimeout = null;
            }
            return;
        }

        // In AMBIENT WAKE-WORD mode (hands-free desktop), debounce silence to auto-commit:
        if (currentInterimTranscript.length > 0) {
            if (speechSilenceTimeout) {
                clearTimeout(speechSilenceTimeout);
                speechSilenceTimeout = null;
            }
            return;
        }

        // If interim is empty and we have accumulated finalized text, start silence debounce timer
        if (accumulatedFinalTranscript && accumulatedFinalTranscript.trim().length > 0 && !isProcessing && !isAssistantSpeaking()) {
            if (speechSilenceTimeout) {
                clearTimeout(speechSilenceTimeout);
            }
            speechSilenceTimeout = setTimeout(() => {
                speechSilenceTimeout = null;
                // Only submit if user hasn't started speaking again and we have final text
                if (!currentInterimTranscript && accumulatedFinalTranscript && !isProcessing && !isAssistantSpeaking() && isListening) {
                    commitAndSubmitTranscript(accumulatedFinalTranscript, "silence_timeout");
                }
            }, 1500); // 1.5s silence debounce for hands-free wake word
        }
    };

    rec.onerror = (event) => {
        console.warn("[Mic] Speech recognition notice:", event.error);
        if (event.error === 'not-allowed') {
            if (statusText) statusText.textContent = "Microphone access blocked. Address bar me Mic allow karein.";
            alert("Microphone permission blocked! Please allow microphone access in your browser address bar.");
            stopListening();
        } else if (event.error === 'no-speech') {
            console.log("[Mic] No speech detected in window.");
        }
    };

    rec.onend = () => {
        speechRecognitionSessionActive = false;
        console.log("[Mic] Speech recognition ended. Accumulated final transcript:", accumulatedFinalTranscript || "(none)", "voiceState:", voiceState);
        console.log('[STT] onend: session=' + recognitionSessionId);

        // If user manually requested stop, suppress auto-restart and discard transcripts!
        if (userRequestedStop) {
            console.log("[Mic] Speech recognition ended following manual user stop. Staying OFF.");
            manualStopRequested = false;
            userRequestedStop = false;
            manualVoiceSession = false;
            recognitionMode = RECOGNITION_MODE.NONE;
            clearRecognitionRestartTimer();
            setVoiceState('idle');
            return;
        }
        if (manualStopRequested) {
            console.log("[Mic] Speech recognition ended following manual stop requested. Staying OFF.");
            manualStopRequested = false;
            manualVoiceSession = false;
            recognitionMode = RECOGNITION_MODE.NONE;
            clearRecognitionRestartTimer();
            setVoiceState('idle');
            return;
        }

        // If assistant is currently speaking or processing, do NOT restart and do NOT commit transcripts!
        if (isTTSPlaying || isSpeaking || isAssistantSpeaking() || isProcessing) {
            console.log("[Mic] Speech recognition ended while assistant is speaking or processing. Leaving recognition OFF.");
            return;
        }
        if (voiceState === 'processing') {
            console.log("[Mic] Speech recognition ended while assistant is in voiceState processing. Leaving recognition OFF.");
            return;
        }

        // In MANUAL mode, if the user hasn't pressed stop yet and the engine paused/stopped (e.g. Android Web Speech silence),
        // seamlessly resume recognition for the current manual session so user's remaining speech is not lost!
        if (recognitionMode === RECOGNITION_MODE.MANUAL && !manualStopRequested && voiceState === 'listening' && !commandSubmittedForSession) {
            console.log("[STT] Mobile engine paused while user in MANUAL session; resuming recognition window...");
            try {
                recognition.start();
                speechRecognitionSessionActive = true;
                return;
            } catch (err) {
                console.warn("[STT] Note on manual recognition resume:", err);
            }
        }

        // Flush any accumulated final transcript that was not yet submitted (ambient mode only)
        if (recognitionMode === RECOGNITION_MODE.AMBIENT_WAKEWORD && accumulatedFinalTranscript && accumulatedFinalTranscript.trim().length > 0 && !isProcessing && submittedTranscript !== accumulatedFinalTranscript.trim()) {
            console.log("[Mic] Engine ended with unsubmitted final transcript; committing now.");
            commitAndSubmitTranscript(accumulatedFinalTranscript, "engine_onend");
            return;
        }

        // If Wake Word ambient mode is active, automatically restart listening (desktop only)
        clearRecognitionRestartTimer();
        if (!userRequestedStop && !manualStopRequested && !manualVoiceSession && recognitionMode === RECOGNITION_MODE.AMBIENT_WAKEWORD && wakeWordEnabled && !isMobileDevice() && !isProcessing && !isTTSPlaying && !isSpeaking && !isAssistantSpeaking() && voiceState === 'idle') {
            scheduleWakeWordRestart(300);
        } else {
            console.log('[STT] Auto restart blocked: manual session');
            setVoiceState('idle');
            recognitionMode = RECOGNITION_MODE.NONE;
        }
    };

    return rec;
}

// 2. State & Orb Animations Controller (Supports .orb-listening, .orb-thinking, .orb-speaking, .orb-idle, .orb-error)
function setOrbState(state) {
    if (!orbWrapper) return;
    orbWrapper.classList.remove(
        'listening', 'thinking', 'speaking', 'idle', 'error',
        'orb-listening', 'orb-thinking', 'orb-speaking', 'orb-idle', 'orb-error'
    );
    if (state !== 'idle') {
        orbWrapper.classList.add(state);
        orbWrapper.classList.add(`orb-${state}`);
    } else {
        orbWrapper.classList.add('idle');
        orbWrapper.classList.add('orb-idle');
    }

    if (state === 'idle') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-cyan)";
        if (statusText) statusText.textContent = "Tap to speak";
    } else if (state === 'listening') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-emerald)";
        if (statusText) statusText.textContent = "Listening...";
    } else if (state === 'thinking' || state === 'processing') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-purple)";
        if (statusText) statusText.textContent = "Thinking...";
    } else if (state === 'speaking') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-blue)";
        if (statusText) statusText.textContent = "Speaking...";
    } else if (state === 'error') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-rose, #ef4444)";
        if (statusText) statusText.textContent = "Something went wrong";
    }
}

// Helpers for Rich Code Formatting & Inline Syntax Highlighting in Chat Bubbles
function renderInlineTextAndCode(container, text) {
    const parts = text.split(/(`[^`]+`)/g);
    for (const part of parts) {
        if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
            const code = document.createElement('code');
            code.textContent = part.slice(1, -1);
            container.appendChild(code);
        } else if (part) {
            const span = document.createElement('span');
            span.textContent = part;
            container.appendChild(span);
        }
    }
}

function renderFormattedMessage(container, text) {
    if (!text) return;
    // Regex matches Markdown code fences: ```(lang)?\n([\s\S]*?)```
    const codeBlockRegex = /```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    while ((match = codeBlockRegex.exec(text)) !== null) {
        const textBefore = text.slice(lastIndex, match.index);
        if (textBefore) {
            renderInlineTextAndCode(container, textBefore);
        }

        const lang = match[1] || 'code';
        const codeSnippet = match[2];

        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';

        const header = document.createElement('div');
        header.className = 'code-block-header';

        const langLabel = document.createElement('span');
        langLabel.textContent = lang;

        const copyBtn = document.createElement('button');
        copyBtn.className = 'code-copy-btn';
        copyBtn.type = 'button';
        copyBtn.textContent = 'Copy';
        copyBtn.setAttribute('aria-label', `Copy ${lang} code`);
        copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(codeSnippet).then(() => {
                copyBtn.textContent = 'Copied!';
                setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
            }).catch(() => {
                copyBtn.textContent = 'Failed';
            });
        });

        header.appendChild(langLabel);
        header.appendChild(copyBtn);

        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = codeSnippet;
        pre.appendChild(code);

        wrapper.appendChild(header);
        wrapper.appendChild(pre);
        container.appendChild(wrapper);

        lastIndex = codeBlockRegex.lastIndex;
    }

    const textRemaining = text.slice(lastIndex);
    if (textRemaining) {
        renderInlineTextAndCode(container, textRemaining);
    }
}

// Dynamically Append Messages to Multi-Line Chat History with Headers, Timestamps & Code Formatting
function appendChatMessage(role, text) {
    if (!chatMessages || !text) return;

    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;

    const header = document.createElement('div');
    header.className = 'bubble-header';

    const sender = document.createElement('span');
    sender.className = 'bubble-sender';
    sender.textContent = role === 'user' ? 'You' : 'Astra';

    const time = document.createElement('span');
    time.className = 'bubble-time';
    const now = new Date();
    time.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    header.appendChild(sender);
    header.appendChild(time);

    const content = document.createElement('div');
    content.className = 'bubble-text';
    renderFormattedMessage(content, text);

    bubble.appendChild(header);
    bubble.appendChild(content);
    chatMessages.appendChild(bubble);

    // Smooth auto-scroll to newest message at the bottom
    chatMessages.scrollTo({
        top: chatMessages.scrollHeight,
        behavior: 'smooth'
    });
}

// =====================================================================
// Robust Mobile Voice Recording Pipeline (MediaRecorder + getUserMedia)
// =====================================================================

function getSupportedMimeType() {
    if (typeof MediaRecorder === 'undefined') return '';
    const candidates = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/mp4',
        'audio/ogg;codecs=opus',
        'audio/ogg',
        'audio/wav'
    ];
    for (const mime of candidates) {
        if (MediaRecorder.isTypeSupported(mime)) {
            return mime;
        }
    }
    return '';
}

// Dedicated audio teardown and reset function
function stopAndResetAudio(reason = 'reset') {
    activePlaybackId++;
    console.log(`[Audio] Playback invalidated requestId=${activeRequestId} (reason: ${reason})`);

    // 1. Clear any pending play timers
    if (pendingPlaybackTimeout) {
        clearTimeout(pendingPlaybackTimeout);
        pendingPlaybackTimeout = null;
    }

    // 2. Remove document touch/click unlock listeners to prevent stale taps from playing
    if (activeUnlockAudioHandler) {
        document.removeEventListener('click', activeUnlockAudioHandler);
        document.removeEventListener('touchstart', activeUnlockAudioHandler);
        activeUnlockAudioHandler = null;
    }

    // 3. Remove any manual tap-to-listen button in chat
    if (chatMessages) {
        const tapBtns = chatMessages.querySelectorAll('.listen-tap-btn');
        tapBtns.forEach(btn => btn.remove());
    }

    // 4. Detach player handlers and purge media decoder buffer
    if (audioPlayer) {
        audioPlayer.onplay = null;
        audioPlayer.onended = null;
        audioPlayer.onpause = null;
        audioPlayer.onerror = null;

        try {
            audioPlayer.pause();
        } catch (e) {}

        try {
            audioPlayer.currentTime = 0;
        } catch (e) {}

        // Complete unload to purge buffer in mobile browsers
        audioPlayer.removeAttribute('src');
        audioPlayer.src = '';
        try {
            audioPlayer.load();
        } catch (e) {}
    }

    // 5. Reset speaking flags
    isTTSPlaying = false;
    isSpeaking = false;
    updateInterruptUI(false);
}

function primeAudioPlayback() {
    // Safely prime mobile audio with silent WAV to establish user-gesture permission
    // without ever replaying previous speech
    if (audioPlayer && !isAssistantSpeaking()) {
        try {
            audioPlayer.src = SILENT_PRIME_AUDIO;
            const p = audioPlayer.play();
            if (p !== undefined) {
                p.then(() => {
                    audioPlayer.pause();
                    audioPlayer.currentTime = 0;
                }).catch(() => {});
            }
        } catch (e) {}
    }
}

function formatRecordingTime(sec) {
    const m = Math.floor(sec / 60).toString().padStart(2, '0');
    const s = (sec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
}

function updateRecordingStatusTimer() {
    const timeFormatted = formatRecordingTime(recordingSeconds);
    if (statusText) {
        statusText.textContent = `🎙️ Listening... ${timeFormatted}`;
    }
    if (liveTranscript) {
        liveTranscript.textContent = `🎙️ Recording voice (${timeFormatted}) • Tap Mic when done`;
    }
    if (liveTranscriptWrapper) {
        liveTranscriptWrapper.classList.add('active');
    }
}

function cleanupMobileRecording() {
    clearInterval(recordingTimerInterval);
    clearTimeout(recordingSafetyTimeout);
    recordingTimerInterval = null;
    recordingSafetyTimeout = null;

    if (recordingStream) {
        try {
            recordingStream.getTracks().forEach(track => track.stop());
        } catch (e) {}
        recordingStream = null;
    }

    audioChunks = [];
    isRecordingAudio = false;
    isListening = false;
    updateMicrophoneUI(false);
}

let isMediaInitializing = false;

async function startMobileRecording() {
    if (isMediaInitializing || isRecordingAudio || isProcessing) {
        console.warn("[MediaRecorder] Recording or initialization already active.");
        return;
    }
    isMediaInitializing = true;

    // Assign new request and recording ID, invalidate prior playback, and cancel pending tasks
    const requestId = ++activeRequestId;
    activeRecordingRequestId = requestId;
    console.log(`[VoiceRequest] Started requestId=${requestId}`);
    stopAndResetAudio('start_mobile_recording');

    // 1. Prime mobile audio element within user tap gesture context safely
    primeAudioPlayback();

    // 2. HTTPS / Security origin verification
    const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    const isSecure = window.isSecureContext || isLocalhost || window.location.protocol.includes('https');
    if (!isSecure) {
        console.warn("[MediaRecorder] Microphone on mobile/LAN requires HTTPS context.");
        if (cachedTunnelUrl) {
            if (statusText) {
                statusText.innerHTML = "⚠️ Mobile mic requires HTTPS. <a href='" + cachedTunnelUrl + "' style='color:#60a5fa;text-decoration:underline;font-weight:600;'>Tap here to switch to Secure HTTPS</a>";
            }
            const confirmSwitch = confirm("Microphone on mobile requires HTTPS for browser security.\n\nWould you like to switch to the Secure Cloudflare Tunnel now?");
            if (confirmSwitch) {
                window.location.href = cachedTunnelUrl;
                return;
            }
        } else {
            if (statusText) statusText.textContent = "⚠️ Mobile mic requires HTTPS. Check terminal for Cloudflare URL.";
            alert("Microphone on mobile requires HTTPS.\nPlease connect via the Cloudflare Tunnel HTTPS URL printed in your terminal or enable USE_TUNNEL=true in .env.");
        }
        return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert("Audio recording is not supported on this browser/environment. Please use text input.");
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });

        // Guard: If another request started while getUserMedia was resolving, abort stream immediately
        if (userRequestedStop || manualStopRequested || activeRecordingRequestId !== requestId || activeRequestId !== requestId) {
            try { stream.getTracks().forEach(track => track.stop()); } catch (e) {}
            return;
        }

        recordingStream = stream;
        audioChunks = [];
        let hasSubmittedCurrentRecording = false;
        const currentSessionId = ++voiceSessionId;

        // 3. Detect and pick supported MIME type
        const detectedMime = getSupportedMimeType();
        const options = detectedMime ? { mimeType: detectedMime } : {};
        console.log("[MediaRecorder] Initializing mobile recording with format:", detectedMime || "browser-default");

        mediaRecorder = new MediaRecorder(stream, options);

        mediaRecorder.ondataavailable = (event) => {
            if (activeRecordingRequestId !== requestId) return;
            if (event.data && event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onerror = (err) => {
            console.error("[MediaRecorder] Recording error:", err);
            cleanupMobileRecording();
            setOrbState('idle');
            if (statusText) statusText.textContent = "Recording error: " + (err.error ? err.error.message : err.message);
        };

        mediaRecorder.onstop = async () => {
            clearInterval(recordingTimerInterval);
            clearTimeout(recordingSafetyTimeout);
            recordingTimerInterval = null;
            recordingSafetyTimeout = null;

            // Stop all hardware tracks immediately
            if (recordingStream) {
                try {
                    recordingStream.getTracks().forEach(track => track.stop());
                } catch (e) {}
                recordingStream = null;
            }

            // Discard recording if invalidated by a newer request or cancelled
            if (activeRecordingRequestId !== requestId || activeRequestId !== requestId) {
                console.warn(`[VoiceRequest] Discarding recording for stale requestId=${requestId} (active=${activeRequestId})`);
                audioChunks = [];
                return;
            }

            if (hasSubmittedCurrentRecording) {
                console.warn(`[VoiceRequest] Ignoring duplicate submission for requestId=${requestId}`);
                return;
            }

            if (audioChunks.length === 0) {
                console.warn("[MediaRecorder] No audio chunks captured.");
                setVoiceState('idle');
                setOrbState('idle');
                if (statusText) statusText.textContent = "Astra Ready • Click Mic to Talk";
                return;
            }

            const chosenMime = mediaRecorder.mimeType || detectedMime || 'audio/webm';
            const audioBlob = new Blob(audioChunks, { type: chosenMime });
            audioChunks = [];

            if (audioBlob.size < 100) {
                console.warn("[MediaRecorder] Audio payload too small (empty or silent):", audioBlob.size, "bytes");
                setVoiceState('idle');
                setOrbState('idle');
                if (statusText) statusText.textContent = "Koi aawaz capture nahi hui. Kripya dobara bolein.";
                return;
            }

            hasSubmittedCurrentRecording = true;
            // Transition to PROCESSING state and upload complete recording
            await sendVoiceUpload(audioBlob, chosenMime, requestId);
        };

        // Start recording with 250ms chunks for continuous streaming buffer
        mediaRecorder.start(250);
        isRecordingAudio = true;
        isListening = true;
        userRequestedStop = false;
        console.log(`[VoiceRequest] Recording started requestId=${requestId}`);

        setVoiceState('listening');
        updateMicrophoneUI(true);
        if (micBtn) {
            micBtn.classList.add('recording');
        }
        setOrbState('listening');

        // Start live elapsed timer: 00:00, 00:01, ...
        recordingSeconds = 0;
        updateRecordingStatusTimer();
        recordingTimerInterval = setInterval(() => {
            recordingSeconds++;
            updateRecordingStatusTimer();
        }, 1000);

        // Safety timeout: max 60s recording to prevent accidental background drain
        recordingSafetyTimeout = setTimeout(() => {
            if (isRecordingAudio) {
                console.log("[MediaRecorder] 60s max recording timeout reached. Auto-stopping.");
                stopMobileRecording();
            }
        }, 60000);

    } catch (err) {
        console.error("[MediaRecorder] getUserMedia error:", err);
        cleanupMobileRecording();
        setOrbState('idle');
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
            if (statusText) statusText.textContent = "⚠️ Mic permission denied. Address bar me allow karein.";
            alert("Microphone permission denied. Please allow microphone access in your mobile browser settings.");
        } else if (err.name === 'NotFoundError') {
            if (statusText) statusText.textContent = "⚠️ Koi microphone nahi mila.";
        } else {
            if (statusText) statusText.textContent = "Mic access error: " + err.message;
        }
    } finally {
        isMediaInitializing = false;
    }
}

function stopMobileRecording(isManualUserStop = false) {
    if (!isRecordingAudio) return;
    console.log(`[VoiceRequest] Recording stopped requestId=${activeRecordingRequestId}`);
    console.log("[MediaRecorder] Stopping mobile recording after", recordingSeconds, "seconds (manual:", isManualUserStop, ")");

    clearInterval(recordingTimerInterval);
    clearTimeout(recordingSafetyTimeout);
    recordingTimerInterval = null;
    recordingSafetyTimeout = null;

    isRecordingAudio = false;
    isListening = false;
    updateMicrophoneUI(false);

    if (isManualUserStop) {
        manualStopRequested = true;
        userRequestedStop = true;
        audioChunks = [];
        if (mediaRecorder) {
            mediaRecorder.onstop = null;
            if (mediaRecorder.state !== 'inactive') {
                try { mediaRecorder.stop(); } catch (e) {}
            }
        }
        cleanupMobileRecording();
        if (liveTranscript) liveTranscript.textContent = '';
        if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');
        setVoiceState('idle');
        return;
    }

    // Immediately transition to PROCESSING state
    setVoiceState('processing');
    if (statusText) {
        statusText.textContent = "⏳ Transcribing & thinking with Gemini...";
    }
    if (liveTranscript) {
        liveTranscript.textContent = "⏳ Transcribing & thinking with Gemini (Hinglish/Hindi/English)...";
    }

    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        try {
            if (typeof mediaRecorder.requestData === 'function' && mediaRecorder.state === 'recording') {
                mediaRecorder.requestData();
            }
        } catch (e) {
            console.warn("[MediaRecorder] requestData error:", e);
        }
        try {
            mediaRecorder.stop();
        } catch (e) {
            console.warn("[MediaRecorder] stop error:", e);
        }
    }
}

async function sendVoiceUpload(audioBlob, mimeType, existingRequestId = null) {
    const requestId = existingRequestId || ++activeRequestId;
    activeRecordingRequestId = requestId;

    if (activeVoiceUploadAbortController) {
        try { activeVoiceUploadAbortController.abort('superseded'); } catch (e) {}
    }
    const controller = new AbortController();
    activeVoiceUploadAbortController = controller;

    console.log(`[VoiceRequest] Upload started requestId=${requestId}`);
    isProcessing = true;
    currentlyProcessingTranscript = 'Voice upload';
    setOrbState('thinking');
    if (statusText) statusText.textContent = "⏳ Processing voice...";

    const formData = new FormData();
    const ext = mimeType.includes('mp4') ? 'mp4' : (mimeType.includes('ogg') ? 'ogg' : (mimeType.includes('wav') ? 'wav' : 'webm'));
    formData.append('audio_file', audioBlob, `recording_${Date.now()}.${ext}`);
    formData.append('session_id', astraSessionId);
    formData.append('lang', currentLang);
    formData.append('is_mobile', /Android|iPhone|iPad|iPod/i.test(navigator.userAgent));

    let timeoutTimer = null;
    let didTimeout = false;

    try {
        timeoutTimer = setTimeout(() => {
            didTimeout = true;
            try { controller.abort('timeout'); } catch (e) {}
        }, FETCH_TIMEOUT_MS);

        const response = await fetch('/api/voice_upload', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-Astra-Token': astraToken
            },
            body: formData,
            signal: controller.signal
        });

        clearTimeout(timeoutTimer);
        timeoutTimer = null;

        // Discard stale response if newer request began or was cancelled
        if (requestId !== activeRequestId) {
            console.warn(`[Astra] Stale voice upload response discarded for request #${requestId}`);
            return;
        }

        if (!response.ok) {
            throw new Error(`Server returned HTTP ${response.status}`);
        }

        const data = await response.json();
        console.log(`[VoiceRequest] Response received requestId=${requestId}`, data);

        if (requestId !== activeRequestId) return;

        if (liveTranscript) liveTranscript.textContent = '';
        if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');

        // Handle transcription failure or error from backend
        if (data.success === false) {
            console.warn("[Astra] Voice upload transcription failed:", data.error || data.detail);
            const userMsg = data.message || "Sorry, I couldn't understand that. Please try again.";
            if (statusText) {
                statusText.textContent = `⚠️ ${userMsg}`;
            }
            setOrbState('idle');
            isProcessing = false;
            setVoiceState('idle');
            currentlyProcessingTranscript = '';
            manualVoiceSession = false;
            return;
        }

        const userTranscript = data.transcript || data.transcription;
        if (userTranscript) {
            appendChatMessage('user', userTranscript);
        }
        if (data.reply) {
            appendChatMessage('assistant', data.reply);
            if (assistantReply) {
                assistantReply.textContent = `"${data.reply}"`;
            }
        }

        if (data.audio_url) {
            playAudioResponse(data.audio_url, requestId);
        } else {
            if (data.tts_failed || data.tts_error) {
                console.warn("[Astra] Voice upload TTS synthesis unavailable; text response preserved.");
            }
            setOrbState('idle');
            isProcessing = false;
            setVoiceState('idle');
            currentlyProcessingTranscript = '';
            manualVoiceSession = false;
        }

    } catch (err) {
        if (timeoutTimer) {
            clearTimeout(timeoutTimer);
            timeoutTimer = null;
        }

        if (requestId !== activeRequestId) {
            console.warn(`[Astra] Stale voice upload error discarded for request #${requestId}`);
            return;
        }

        console.error("[Astra] Voice upload error:", err);
        const isTimeout = didTimeout || (controller.signal.aborted && controller.signal.reason === 'timeout') || err.name === 'AbortError';
        const userNotice = isTimeout
            ? "Voice upload timeout ho gaya. Kripya dobara bolein."
            : "Voice processing failed. Please try again.";

        if (statusText) {
            statusText.textContent = `⚠️ ${userNotice}`;
        }
        setOrbState('idle');
        isProcessing = false;
        setVoiceState('idle');
        currentlyProcessingTranscript = '';
        manualVoiceSession = false;
        if (liveTranscript) liveTranscript.textContent = '';
        if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');
    } finally {
        if (timeoutTimer) {
            clearTimeout(timeoutTimer);
        }
        if (activeVoiceUploadAbortController === controller) {
            activeVoiceUploadAbortController = null;
        }
    }
}

function startListening(forceInterrupt = false) {
    if (isTTSPlaying || isSpeaking || isAssistantSpeaking()) {
        if (!forceInterrupt) {
            console.log("[Mic] startListening ignored because assistant is speaking.");
            return;
        }
        interruptPlayback(true);
        return;
    }

    if (isProcessing) {
        if (!forceInterrupt) {
            console.log("[Mic] startListening ignored because assistant is processing.");
            return;
        }
    }

    // If recognition is already active on desktop, ignore redundant start calls
    if (isListening && speechRecognitionSessionActive) {
        console.log("[Mic] startListening: Recognition session already active; ignoring redundant start.");
        return;
    }

    // Purge any stale audio session before opening the microphone
    stopAndResetAudio('start_listening');

    // Clear any pending ambient restart timer
    clearRecognitionRestartTimer();

    // Reset user requested stop flag on fresh manual/explicit start
    manualStopRequested = false;
    userRequestedStop = false;

    // Reset speech recognition buffers for fresh voice interaction
    // (Preserve submittedTranscript & lastSubmissionTime to maintain 2.5s deduplication memory)
    accumulatedFinalTranscript = '';
    currentInterimTranscript = '';
    if (speechSilenceTimeout) {
        clearTimeout(speechSilenceTimeout);
        speechSilenceTimeout = null;
    }

    // Mobile browsers (Android Chrome/PWA, iOS Safari, etc.):
    // Route directly to MediaRecorder + Gemini Multi-lingual STT for superior Hinglish/Hindi/English accuracy
    // and to eliminate mobile Web Speech API pauses, restarts, and dropped words.
    if (isMobileDevice()) {
        console.log("[Mic] Mobile device detected: Routing to MediaRecorder + Gemini Multi-lingual STT.");
        startMobileRecording();
        return;
    }

    // Check for native Web Speech API (available on Desktop Chrome, Edge, etc.)
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        // Fallback for browsers without Web Speech API
        startMobileRecording();
        return;
    }

    if (!recognition) {
        recognition = initSpeechRecognition();
    }
    if (!recognition) {
        startMobileRecording();
        return;
    }

    if (isTTSPlaying || isSpeaking || isAssistantSpeaking()) {
        console.log("[Mic] recognition.start aborted because assistant is speaking.");
        return;
    }

    console.log('[Mic] Starting...');
    console.log('[STT] Session started:', recognitionSessionId, 'lang:', currentLang, 'continuous:', true, 'interimResults:', true);

    try {
        recognition.lang = currentLang;
        updateInterruptUI(false);
        updateMicrophoneUI(true);
        setVoiceState('listening');
        recognition.start();
    } catch (err) {
        if (err.name === 'InvalidStateError' || (err.message && err.message.includes('already started'))) {
            console.log("[Mic] recognition is already active; duplicate start ignored.");
            updateMicrophoneUI(true);
            setVoiceState('listening');
            return;
        }
        console.warn("[Mic] recognition.start note:", err);
        // Fallback to MediaRecorder only if recognition initialization genuinely failed
        if (!speechRecognitionSessionActive && !isListening) {
            startMobileRecording();
        }
    }
}

function stopListening(isManualUserStop = false) {
    clearRecognitionRestartTimer();
    console.log('[Mic] Manual stop...');
    if (isManualUserStop) {
        console.log('[STT] Manual stop requested: session=' + recognitionSessionId);
    }

    if (speechSilenceTimeout) {
        clearTimeout(speechSilenceTimeout);
        speechSilenceTimeout = null;
    }

    if (isProcessing || voiceState === 'processing') {
        updateMicrophoneUI(false);
        if (recognition) {
            try { recognition.stop(); } catch (e) {}
        }
        return;
    }

    if (isRecordingAudio) {
        stopMobileRecording(false);
        return;
    }

    // Check if user spoke anything (final, interim transcript, or live displayed text)
    const liveText = liveTranscript ? liveTranscript.textContent.replace(/^✦\s*\w+\s*Listening\.\.\./i, '').trim() : '';
    let textToCommit = mergeTranscripts(accumulatedFinalTranscript, currentInterimTranscript);
    if (!textToCommit && liveText && !liveText.includes("Listening...") && !liveText.includes("Uploading") && !liveText.includes("Processing")) {
        textToCommit = liveText;
    }

    if (textToCommit && textToCommit.length > 0 && !isAssistantSpeaking()) {
        console.log("[Mic] stopListening: Submitting captured speech on user stop:", textToCommit);
        updateMicrophoneUI(false);
        if (recognition) {
            try { recognition.stop(); } catch (e) {}
        }
        commitAndSubmitTranscript(textToCommit, "user_stop");
        return;
    }

    if (isManualUserStop) {
        console.log("[Mic] stopListening: User manually requested stop with no speech. Aborting recognition and discarding partial text.");
        manualStopRequested = true;
        userRequestedStop = true;
        accumulatedFinalTranscript = '';
        currentInterimTranscript = '';
        if (liveTranscript) liveTranscript.textContent = '';
        if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');
        setVoiceState('stopping');
        updateMicrophoneUI(false);
        if (recognition) {
            try {
                recognition.abort();
            } catch (e) {}
        }
        setVoiceState('idle');
        setTimeout(() => {
            if (voiceState === 'stopping') {
                setVoiceState('idle');
            }
        }, 300);
        return;
    }

    // If non-manual stop (e.g. timeout / push-to-talk release) has captured final text, commit it
    if (accumulatedFinalTranscript && accumulatedFinalTranscript.trim().length > 0 && !isProcessing && !isAssistantSpeaking() && submittedTranscript !== accumulatedFinalTranscript.trim()) {
        const textToCommit2 = accumulatedFinalTranscript;
        setVoiceState('idle');
        updateMicrophoneUI(false);
        if (recognition) {
            try { recognition.stop(); } catch (e) {}
        }
        commitAndSubmitTranscript(textToCommit2, "user_stop");
        return;
    }

    setVoiceState('idle');
    updateMicrophoneUI(false);
    if (recognition) {
        try {
            recognition.stop();
        } catch (e) {}
    }
}

// 3. Integration Layer: Send voice/text command to FastAPI (Authenticated & Multi-Turn Memory)
async function sendVoiceCommand(commandText) {
    if (!commandText || !commandText.trim()) {
        console.warn("[Astra] Suppressing submission of empty or whitespace-only command.");
        isProcessing = false;
        currentlyProcessingTranscript = '';
        setOrbState('idle');
        return;
    }

    const cleanCommand = commandText.trim();

    // Guard: Prevent concurrent request dispatch if already processing another command
    if (isProcessing && currentlyProcessingTranscript && currentlyProcessingTranscript !== cleanCommand) {
        console.warn("[Astra] sendVoiceCommand blocked: another command is already processing:", currentlyProcessingTranscript);
        return;
    }

    // Increment request ID and setup abort controller for timeout and cancellation
    const requestId = ++activeRequestId;
    if (activeChatAbortController) {
        try { activeChatAbortController.abort('superseded'); } catch (e) {}
    }
    const controller = new AbortController();
    activeChatAbortController = controller;

    // Lock processing state early before aborting recognition or triggering network requests
    isProcessing = true;
    currentlyProcessingTranscript = cleanCommand;
    submittedTranscript = cleanCommand;
    lastSubmissionTime = Date.now();

    // Immediately cancel any pending wake word restart and stop recognition
    if (wakeWordRestartTimeout) {
        clearTimeout(wakeWordRestartTimeout);
        wakeWordRestartTimeout = null;
    }
    if (recognition) {
        try { recognition.abort(); } catch (e) {}
    }

    // Force clear text input immediately so voice or chip commands never leave text in the box
    if (textCommandInput) {
        textCommandInput.value = '';
    }

    console.log("[Astra] Backend request started for command:", cleanCommand, "Session:", astraSessionId, "ReqId:", requestId);

    // Dynamically append user message to chat history & clear live interim transcript
    appendChatMessage('user', cleanCommand);
    if (liveTranscript) liveTranscript.textContent = '';
    if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');

    setOrbState('thinking');
    if (statusText) statusText.textContent = "Thinking...";

    let timeoutTimer = null;
    let didTimeout = false;

    try {
        timeoutTimer = setTimeout(() => {
            didTimeout = true;
            try { controller.abort('timeout'); } catch (e) {}
        }, FETCH_TIMEOUT_MS);

        const response = await fetch('/api/chat', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Astra-Token': astraToken
            },
            body: JSON.stringify({
                text: cleanCommand,
                lang: currentLang,
                session_id: astraSessionId,
                is_mobile: /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
            }),
            signal: controller.signal
        });

        clearTimeout(timeoutTimer);
        timeoutTimer = null;

        // Discard stale response if newer request began or was cancelled
        if (requestId !== activeRequestId) {
            console.warn(`[Astra] Discarding stale response for request #${requestId} (active is #${activeRequestId})`);
            return;
        }

        if (!response.ok) {
            if (response.status === 401) {
                throw new Error("Authentication failed: Invalid security token.");
            }
            throw new Error(`Server returned HTTP ${response.status}`);
        }

        const data = await response.json();
        console.log("[Astra] Backend request ended. Success:", data.success, "Data:", data);

        if (requestId !== activeRequestId) {
            console.warn(`[Astra] Discarding stale JSON payload for request #${requestId}`);
            return;
        }

        // Handle transcription failure or error response from backend
        if (data.success === false) {
            console.warn("[Astra] Backend returned failure:", data.error || data.detail);
            const userMsg = data.message || "Sorry, I couldn't understand that. Please try again.";
            if (statusText) {
                statusText.textContent = `⚠️ ${userMsg}`;
            }
            setVoiceState('idle');
            isProcessing = false;
            currentlyProcessingTranscript = '';
            manualVoiceSession = false;
            if (wakeWordEnabled && !isMobileDevice() && !manualStopRequested && !userRequestedStop) scheduleWakeWordRestart(300);
            return;
        }

        // Dynamically append assistant response to chat history
        if (data.reply) {
            appendChatMessage('assistant', data.reply);
            if (assistantReply) {
                assistantReply.textContent = `"${data.reply}"`;
            }
        }

        // Play generated Edge-TTS audio
        if (data.audio_url) {
            playAudioResponse(data.audio_url, requestId);
        } else {
            if (data.tts_failed || data.tts_error) {
                console.warn("[Astra] Assistant TTS synthesis unavailable; text response preserved.");
            }
            setOrbState('idle');
            setVoiceState('idle');
            isProcessing = false;
            currentlyProcessingTranscript = '';
            manualVoiceSession = false;
            if (wakeWordEnabled && !isMobileDevice() && !manualStopRequested && !userRequestedStop) scheduleWakeWordRestart(300);
        }

    } catch (error) {
        if (timeoutTimer) {
            clearTimeout(timeoutTimer);
            timeoutTimer = null;
        }

        // Discard error if this request has already been superseded
        if (requestId !== activeRequestId) {
            console.warn(`[Astra] Discarding error from superseded request #${requestId}`);
            return;
        }

        console.error("[Astra] Backend communication error:", error);
        const isTimeout = didTimeout || (controller.signal.aborted && controller.signal.reason === 'timeout') || error.name === 'AbortError';
        const userNotice = isTimeout
            ? "Command timeout ho gaya. Kripya dobara bolein."
            : "Connection error. Kripya dobara koshish karein.";

        if (statusText) {
            statusText.textContent = `⚠️ ${userNotice}`;
        }
        appendChatMessage('assistant', userNotice);
        if (assistantReply) {
            assistantReply.textContent = `"${userNotice}"`;
        }

        setOrbState('idle');
        setVoiceState('idle');
        isProcessing = false;
        currentlyProcessingTranscript = '';
        manualVoiceSession = false;
        if (wakeWordEnabled && !isMobileDevice() && !manualStopRequested && !userRequestedStop) scheduleWakeWordRestart(300);
    } finally {
        if (timeoutTimer) {
            clearTimeout(timeoutTimer);
        }
        if (activeChatAbortController === controller) {
            activeChatAbortController = null;
        }
    }
}

// Update Interrupt UI visibility
function updateInterruptUI(isSpeaking) {
    const interruptBtn = document.getElementById('interruptBtn');
    if (!interruptBtn) return;
    if (isSpeaking) {
        interruptBtn.classList.remove('hidden');
    } else {
        interruptBtn.classList.add('hidden');
    }
}

// Instant Interruption Function
function interruptPlayback(startListeningNow = false) {
    console.log("[Astra] Interrupting assistant audio playback. startListeningNow =", startListeningNow);
    // Invalidate and cancel any in-flight requests immediately
    activeRequestId++;
    if (activeChatAbortController) {
        try { activeChatAbortController.abort('interrupted'); } catch (e) {}
        activeChatAbortController = null;
    }
    if (activeVoiceUploadAbortController) {
        try { activeVoiceUploadAbortController.abort('interrupted'); } catch (e) {}
        activeVoiceUploadAbortController = null;
    }

    stopAndResetAudio('interrupt_playback');
    isTTSPlaying = false;
    isSpeaking = false;
    clearRecognitionRestartTimer();
    if (audioPlayer) {
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
        audioPlayer.removeAttribute('src');
    }
    updateInterruptUI(false);
    isProcessing = false;
    currentlyProcessingTranscript = '';
    manualVoiceSession = false;
    setVoiceState('idle');

    if (startListeningNow) {
        startListening(true);
    } else {
        stopListening();
        setOrbState('idle');
        if (statusText) statusText.textContent = "Interrupted • Click Mic or Press Spacebar";
        if (wakeWordEnabled && !isMobileDevice() && !manualStopRequested && !userRequestedStop) scheduleWakeWordRestart(500);
    }
}

// 4. Play Audio Response using HTML5 Audio (Buffers fully to prevent stutter)
function playAudioResponse(audioUrl, requestId = null) {
    console.log(`[Audio] Playback requested requestId=${requestId !== null ? requestId : 'unspecified'}`);
    console.log(`[Audio] Current active requestId=${activeRequestId}`);

    // If a specific requestId was supplied and does not match the activeRequestId, discard stale audio
    if (requestId !== null && requestId !== activeRequestId) {
        console.warn(`[Audio] Ignoring stale audio requestId=${requestId} (active=${activeRequestId})`);
        return;
    }

    try {
        if (!audioUrl || typeof audioUrl !== 'string') {
            console.warn("[TTS] Invalid audioUrl provided:", audioUrl);
            isTTSPlaying = false;
            isSpeaking = false;
            setOrbState('idle');
            setVoiceState('idle');
            isProcessing = false;
            return;
        }

        // Increment playback generation ID and tear down any prior audio session
        stopAndResetAudio('new_playback_assigned');
        const playbackId = activePlaybackId;

        // Set speaking flags immediately to lock microphone during buffering & playback
        isTTSPlaying = true;
        isSpeaking = true;
        setVoiceState('speaking');

        clearRecognitionRestartTimer();

        // Ensure all microphone input is completely stopped while speaking
        if (recognition) {
            try { recognition.abort(); } catch (e) {}
        }
        if (isRecordingAudio) {
            stopMobileRecording();
        }
        isListening = false;
        speechRecognitionSessionActive = false;
        updateMicrophoneUI(false);

        const fullUrl = audioUrl + (audioUrl.includes('?') ? '&' : '?') + `t=${Date.now()}`;
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
        audioPlayer.preload = "auto";
        audioPlayer.src = fullUrl;
        audioPlayer.load();

        audioPlayer.onplay = () => {
            if (playbackId !== activePlaybackId || (requestId !== null && requestId !== activeRequestId)) return;
            isTTSPlaying = true;
            isSpeaking = true;
            setVoiceState('speaking');
            setOrbState('speaking');
            updateInterruptUI(true);
            if (statusText) statusText.textContent = "Speaking...";
            console.log(`[Audio] Playback started requestId=${requestId !== null ? requestId : 'unspecified'}`);
            console.log("[TTS] Audio playback started.");

            // CRITICAL FIX: DO NOT start speech recognition here.
            // Recognition must remain completely OFF throughout the entire duration of TTS playback.
        };

        audioPlayer.onended = () => {
            if (playbackId !== activePlaybackId) return;
            console.log(`[Audio] Playback ended requestId=${requestId !== null ? requestId : 'unspecified'}`);
            console.log("[TTS] Audio playback completed successfully.");
            isTTSPlaying = false;
            isSpeaking = false;
            audioPlayer.removeAttribute('src');
            audioPlayer.src = '';
            try { audioPlayer.load(); } catch (e) {}
            updateInterruptUI(false);
            setOrbState('idle');
            setVoiceState('idle');
            isProcessing = false;
            currentlyProcessingTranscript = '';
            manualVoiceSession = false;

            // Resume ambient wake-word listening on desktop only when NOT in manual session
            if (wakeWordEnabled && !isMobileDevice() && !manualStopRequested && !userRequestedStop && !manualVoiceSession) {
                scheduleWakeWordRestart(300);
            }
        };

        audioPlayer.onpause = () => {
            // Pause event should NOT reset flags unless audio truly finished or aborted
            if (audioPlayer.ended || !audioPlayer.src) {
                isTTSPlaying = false;
                isSpeaking = false;
                updateInterruptUI(false);
            }
        };

        audioPlayer.onerror = (err) => {
            if (playbackId !== activePlaybackId) return;
            console.warn("[TTS] Audio playback error:", err);
            isTTSPlaying = false;
            isSpeaking = false;
            audioPlayer.removeAttribute('src');
            audioPlayer.src = '';
            try { audioPlayer.load(); } catch (e) {}
            updateInterruptUI(false);
            setOrbState('idle');
            setVoiceState('idle');
            isProcessing = false;
            currentlyProcessingTranscript = '';
            manualVoiceSession = false;
            if (wakeWordEnabled && !isMobileDevice() && !manualStopRequested && !userRequestedStop && !manualVoiceSession) {
                scheduleWakeWordRestart(300);
            }
        };

        let playbackStarted = false;
        const startPlayback = () => {
            if (playbackStarted) return;
            if (playbackId !== activePlaybackId || (requestId !== null && requestId !== activeRequestId)) return;
            playbackStarted = true;
            audioPlayer.removeEventListener('canplaythrough', startPlayback);
            audioPlayer.removeEventListener('canplay', startPlayback);

            const playPromise = audioPlayer.play();
            if (playPromise !== undefined) {
                playPromise.catch(error => {
                    if (playbackId !== activePlaybackId || (requestId !== null && requestId !== activeRequestId)) {
                        return;
                    }
                    console.warn("[TTS] Autoplay blocked by mobile browser policy:", error);
                    isTTSPlaying = false;
                    isSpeaking = false;
                    updateInterruptUI(false);
                    setOrbState('idle');
                    isProcessing = false;

                    // Display friendly tap-to-play prompt for mobile
                    if (statusText) {
                        statusText.textContent = "🔊 Audio tap karein: Sunne ke liye screen ya Mic par tap karein.";
                        statusText.style.color = "#ff4757";
                    }

                    // Screen tap handler to unlock audio and resume speech for this response only
                    const unlockAudio = () => {
                        if (activeUnlockAudioHandler === unlockAudio) {
                            activeUnlockAudioHandler = null;
                        }
                        document.removeEventListener('click', unlockAudio);
                        document.removeEventListener('touchstart', unlockAudio);

                        if (playbackId !== activePlaybackId || (requestId !== null && requestId !== activeRequestId)) {
                            console.warn(`[Audio] Stale unlockAudio tap ignored for playbackId=${playbackId}`);
                            return;
                        }

                        isTTSPlaying = true;
                        isSpeaking = true;
                        setOrbState('speaking');
                        updateInterruptUI(true);
                        audioPlayer.play().then(() => {
                            if (playbackId !== activePlaybackId) return;
                            if (statusText) {
                                statusText.textContent = "Speaking...";
                                statusText.style.color = "";
                            }
                        }).catch(e => {
                            console.log("Audio unlock retry blocked:", e);
                            isTTSPlaying = false;
                            isSpeaking = false;
                            updateInterruptUI(false);
                            setOrbState('idle');
                        });
                    };

                    activeUnlockAudioHandler = unlockAudio;
                    document.addEventListener('click', unlockAudio, { once: true });
                    document.addEventListener('touchstart', unlockAudio, { once: true });

                    appendAudioTapPrompt(audioPlayer, requestId, playbackId);
                });
            }
        };

        // Listen for buffer readiness before beginning playback
        audioPlayer.addEventListener('canplaythrough', startPlayback, { once: true });
        audioPlayer.addEventListener('canplay', startPlayback, { once: true });

        // Fallback safety timeout if canplay event was already dispatched or delayed
        pendingPlaybackTimeout = setTimeout(() => {
            pendingPlaybackTimeout = null;
            if (!playbackStarted && audioPlayer.readyState >= 2 && playbackId === activePlaybackId) {
                startPlayback();
            }
        }, 200);

    } catch (err) {
        console.error("[TTS] Audio player initialization error:", err);
        isTTSPlaying = false;
        isSpeaking = false;
        updateInterruptUI(false);
        setOrbState('idle');
        isProcessing = false;
        currentlyProcessingTranscript = '';
        if (wakeWordEnabled && !isMobileDevice()) startListening();
    }
}

// Append quick tap-to-listen button if mobile browser restricts async audio autoplay
function appendAudioTapPrompt(player, requestId = null, playbackId = null) {
    if (!chatMessages) return;
    const lastMsg = chatMessages.lastElementChild;
    if (lastMsg && lastMsg.classList.contains('assistant')) {
        const existingBtn = lastMsg.querySelector('.listen-tap-btn');
        if (!existingBtn) {
            const btn = document.createElement('button');
            btn.className = 'listen-tap-btn';
            btn.innerHTML = '🔊 Tap to hear voice answer';
            btn.style.cssText = 'margin-top: 8px; padding: 5px 12px; font-size: 0.78rem; border-radius: 12px; background: rgba(59,130,246,0.25); border: 1px solid rgba(59,130,246,0.5); color: #93c5fd; cursor: pointer; display: inline-flex; align-items: center; gap: 4px;';
            btn.onclick = () => {
                if (playbackId !== null && playbackId !== activePlaybackId) {
                    console.warn(`[Audio] Tap-to-hear prompt ignored for stale playbackId=${playbackId}`);
                    btn.remove();
                    return;
                }
                if (requestId !== null && requestId !== activeRequestId) {
                    console.warn(`[Audio] Tap-to-hear prompt ignored for stale requestId=${requestId}`);
                    btn.remove();
                    return;
                }
                isTTSPlaying = true;
                isSpeaking = true;
                setOrbState('speaking');
                updateInterruptUI(true);
                player.play().catch(() => {});
                btn.remove();
            };
            lastMsg.appendChild(btn);
        }
    }
}

// 5. User Input Event Listeners
function handleTextCommandSubmit() {
    if (!textCommandInput) return;
    const commandText = textCommandInput.value.trim();
    if (!commandText) return;

    // Guard against submission while assistant is already processing a command
    if (isProcessing) {
        console.warn("[Astra] Text command submission blocked: assistant is already processing a command.");
        return;
    }

    // Interrupt any active assistant speech/audio immediately
    if (isAssistantSpeaking()) {
        interruptPlayback(false);
    }

    // Clear input field immediately
    textCommandInput.value = '';

    // Dispatch command to backend /api/chat (exact same logic as voice transcript with TTS playback)
    sendVoiceCommand(commandText);
}

if (textCommandForm) {
    textCommandForm.addEventListener('submit', (e) => {
        e.preventDefault();
        handleTextCommandSubmit();
    });
}

if (sendTextBtn) {
    sendTextBtn.addEventListener('click', (e) => {
        e.preventDefault();
        handleTextCommandSubmit();
    });
}

if (textCommandInput) {
    textCommandInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleTextCommandSubmit();
        }
    });
}

const interruptBtn = document.getElementById('interruptBtn');
if (interruptBtn) {
    interruptBtn.addEventListener('click', () => {
        interruptPlayback(false);
    });
}

// Centralized Voice Toggle Handler for micBtn and orbWrapper
let lastVoiceToggleTime = 0;
function handleVoiceToggle(e) {
    const isOrb = (e && e.currentTarget && e.currentTarget.id === 'orbWrapper');
    console.log(isOrb ? '[VoiceUI] ORB CLICK' : '[VoiceUI] MIC CLICK');
    console.log(`[VoiceUI] Toggle from ${isOrb ? 'orb' : 'mic'}`);
    if (e && e.preventDefault && e.cancelable) e.preventDefault();
    const now = Date.now();
    if (now - lastVoiceToggleTime < 350) {
        console.log("[Mic] Ignored rapid voice toggle bounce within 350ms.");
        return;
    }
    lastVoiceToggleTime = now;

    console.log("[Mic] handleVoiceToggle triggered. voiceState:", voiceState, "isRecordingAudio:", isRecordingAudio, "manualVoiceSession:", manualVoiceSession);
    if (voiceState === 'speaking' || isAssistantSpeaking()) {
        interruptPlayback(false);
    } else if (voiceState === 'processing' || isProcessing) {
        console.log("[Mic] Tap ignored while assistant is processing command.");
        return;
    } else if (typeof isMediaInitializing !== 'undefined' && isMediaInitializing) {
        console.log("[Mic] Cancel requested while microphone initialization is pending.");
        userRequestedStop = true;
        manualStopRequested = true;
        activeRecordingRequestId = null;
        setVoiceState('idle');
        return;
    } else if (isRecordingAudio) {
        // Mobile tap-to-talk: clicking mic while recording completes and submits audio to Astra
        manualVoiceSession = false;
        recognitionMode = RECOGNITION_MODE.NONE;
        stopMobileRecording(false);
        return;
    } else if (voiceState === 'listening' || isMicActive()) {
        manualVoiceSession = false;
        stopListening(true);
        return;
    } else if (voiceState === 'stopping') {
        return;
    } else {
        manualVoiceSession = true;
        recognitionMode = RECOGNITION_MODE.MANUAL;
        commandSubmittedForSession = false;
        recognitionSessionId++;
        startListening();
    }
}

if (micBtn) {
    micBtn.addEventListener('click', handleVoiceToggle);
}

// Click on Orb to talk or interrupt
if (orbWrapper) {
    orbWrapper.addEventListener('click', handleVoiceToggle);
}

// Keyboard shortcuts: Space (push to talk / interrupt) & Escape (instant interrupt)
window.addEventListener('keydown', (e) => {
    if (e.code === 'Escape') {
        e.preventDefault();
        interruptPlayback(false);
        return;
    }

    if (e.code === 'Space' && e.target.tagName !== 'INPUT' && !e.repeat) {
        e.preventDefault();
        if (isAssistantSpeaking() || isProcessing) {
            interruptPlayback(false);
        } else if (!isMicActive()) {
            startListening();
        }
    }
});

window.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && e.target.tagName !== 'INPUT') {
        e.preventDefault();
        if (isMicActive() && !wakeWordEnabled) {
            setTimeout(() => stopListening(false), 400);
        }
    }
});

// Wake Word Toggle Button Handler
if (wakeWordToggleBtn) {
    wakeWordToggleBtn.addEventListener('click', () => {
        wakeWordEnabled = !wakeWordEnabled;
        localStorage.setItem('astra_wake_word_enabled', wakeWordEnabled ? 'true' : 'false');
        if (wakeWordEnabled) {
            wakeWordToggleBtn.classList.add('active');
            manualStopRequested = false;
            userRequestedStop = false;
            startListening();
            console.log("[Astra] Ambient Wake-Word mode enabled.");
        } else {
            wakeWordToggleBtn.classList.remove('active');
            clearRecognitionRestartTimer();
            stopListening(true);
            console.log("[Astra] Ambient Wake-Word mode disabled.");
        }
        updateWakeWordUI();
        setOrbState('idle');
    });
}

// Wake Word Trigger Preference Selector Handler
if (wakeWordSelect) {
    wakeWordSelect.addEventListener('change', (e) => {
        wakeWordPreference = e.target.value;
        localStorage.setItem('astra_wake_word_preference', wakeWordPreference);
        updateWakeWordUI();
        console.log("[Astra] Wake word preference set to:", wakeWordPreference);
    });
}

// Language Switcher (हिन्दी / English / Auto)
langBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        langBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const chosen = btn.dataset.lang;
        currentLang = chosen === 'auto' ? 'hi-IN' : chosen;
        if (recognition) {
            recognition.lang = currentLang;
        }
        console.log("[Astra] Language changed to:", currentLang);
    });
});

// Network Status Controller (Online / Offline detection)
function updateNetworkStatus() {
    const pill = document.getElementById('networkStatusPill');
    const text = document.getElementById('networkStatusText');
    if (!pill || !text) return;
    if (navigator.onLine) {
        pill.className = 'status-pill online';
        text.textContent = 'Online';
    } else {
        pill.className = 'status-pill offline';
        text.textContent = 'Offline';
    }
}
window.addEventListener('online', updateNetworkStatus);
window.addEventListener('offline', updateNetworkStatus);

// Fetch & Render Real-Time MCP Status
async function fetchAndRenderMCPStatus() {
    try {
        const res = await fetch('/api/mcp/status', {
            credentials: 'same-origin',
            headers: { 'X-Astra-Token': astraToken }
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        const mcpHeaderBadge = document.getElementById('mcpHeaderBadge');
        const mcpBadgeText = document.getElementById('mcpBadgeText');
        const mcpModalStatusPill = document.getElementById('mcpModalStatusPill');
        const mcpModalStatusText = document.getElementById('mcpModalStatusText');
        const mcpServerName = document.getElementById('mcpServerName');
        const mcpToolCount = document.getElementById('mcpToolCount');
        const mcpLastActivity = document.getElementById('mcpLastActivity');
        const mcpToolsList = document.getElementById('mcpToolsList');
        const mcpActivityFeed = document.getElementById('mcpActivityFeed');

        const statusStr = (data.status || 'Not Configured');
        let statusClass = 'not-configured';
        if (data.enabled && statusStr.toLowerCase().includes('connect')) {
            statusClass = 'connected';
        } else if (data.enabled && statusStr.toLowerCase().includes('disconnect')) {
            statusClass = 'disconnected';
        } else {
            statusClass = 'not-configured';
        }

        if (mcpHeaderBadge) {
            mcpHeaderBadge.className = `mcp-badge ${statusClass}`;
            mcpHeaderBadge.title = `MCP Server: ${statusStr}`;
        }
        if (mcpBadgeText) {
            mcpBadgeText.textContent = `MCP: ${statusStr}`;
        }
        if (mcpModalStatusPill) {
            mcpModalStatusPill.className = `mcp-status-pill ${statusClass}`;
        }
        if (mcpModalStatusText) {
            mcpModalStatusText.textContent = statusStr;
        }
        if (mcpServerName) {
            mcpServerName.textContent = data.server_name || 'None';
        }
        if (mcpToolCount) {
            mcpToolCount.textContent = (data.tool_count || 0) + (data.tool_count === 1 ? ' Tool' : ' Tools');
        }
        if (mcpLastActivity) {
            mcpLastActivity.textContent = data.last_activity || 'No MCP activity yet.';
        }

        // Render discovered tools list
        if (mcpToolsList) {
            mcpToolsList.innerHTML = '';
            if (data.tools && Array.isArray(data.tools) && data.tools.length > 0) {
                data.tools.forEach(tool => {
                    const item = document.createElement('div');
                    item.className = 'mcp-tool-item';
                    const name = document.createElement('span');
                    name.className = 'mcp-tool-name';
                    name.textContent = tool.name || 'Unnamed Tool';
                    const desc = document.createElement('span');
                    desc.className = 'mcp-tool-desc';
                    desc.textContent = tool.description || 'No description provided';
                    item.appendChild(name);
                    item.appendChild(desc);
                    mcpToolsList.appendChild(item);
                });
            } else {
                const empty = document.createElement('div');
                empty.className = 'mcp-empty-state';
                empty.textContent = data.enabled ? 'No tools discovered on server.' : 'MCP not configured. Set MCP_ENABLED=true in .env to connect.';
                mcpToolsList.appendChild(empty);
            }
        }

        // Render activity log feed
        if (mcpActivityFeed) {
            mcpActivityFeed.innerHTML = '';
            if (data.recent_activities && Array.isArray(data.recent_activities) && data.recent_activities.length > 0) {
                data.recent_activities.forEach(act => {
                    const line = document.createElement('div');
                    line.className = 'mcp-log-entry';
                    const time = document.createElement('span');
                    time.className = 'mcp-log-time';
                    time.textContent = act.timestamp ? new Date(act.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
                    const toolName = document.createElement('span');
                    toolName.className = 'mcp-log-tool';
                    toolName.textContent = `[${act.tool || 'event'}]`;
                    const msg = document.createElement('span');
                    msg.className = 'mcp-log-msg';
                    msg.textContent = act.message || act.status || '';
                    line.appendChild(time);
                    line.appendChild(toolName);
                    line.appendChild(msg);
                    mcpActivityFeed.appendChild(line);
                });
            } else {
                const empty = document.createElement('div');
                empty.className = 'mcp-empty-state';
                empty.textContent = 'No MCP tool activity recorded.';
                mcpActivityFeed.appendChild(empty);
            }
        }
    } catch (e) {
        console.warn('[MCP] Failed to fetch MCP status:', e);
        const mcpHeaderBadge = document.getElementById('mcpHeaderBadge');
        const mcpBadgeText = document.getElementById('mcpBadgeText');
        if (mcpHeaderBadge) mcpHeaderBadge.className = 'mcp-badge not-configured';
        if (mcpBadgeText) mcpBadgeText.textContent = 'MCP: Not Configured';
    }
}

// Settings Modal Tabs & Management
function switchSettingsTab(tabName) {
    if (!tabName) return;
    const cleanTab = tabName.replace(/^tab-/, '');
    const tabBtns = document.querySelectorAll('.console-tabs .tab-btn');
    const tabPanes = document.querySelectorAll('.modal-body .tab-pane');

    tabBtns.forEach(btn => {
        const target = (btn.getAttribute('data-tab') || '').replace(/^tab-/, '');
        const isMatch = target === cleanTab;
        btn.classList.toggle('active', isMatch);
        btn.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });

    tabPanes.forEach(pane => {
        const paneId = pane.id.replace(/^tab-/, '');
        const isMatch = paneId === cleanTab;
        pane.classList.toggle('active', isMatch);
    });
}

function openSettingsModal(targetTab = null) {
    if (!settingsModal) return;
    settingsModal.classList.remove('hidden');
    settingsModal.classList.add('open');
    if (targetTab) {
        switchSettingsTab(targetTab);
    }
    if (saveStatus) saveStatus.textContent = "";
    if (wakeWordSelect) wakeWordSelect.value = wakeWordPreference;
    fetchAndRenderMCPStatus();
    loadSettingsStatus();
}

function closeSettingsModal() {
    if (!settingsModal) return;
    settingsModal.classList.add('hidden');
    settingsModal.classList.remove('open');
}

async function loadSettingsStatus() {
    try {
        const res = await fetch('/api/status', {
            credentials: 'same-origin',
            headers: { 'X-Astra-Token': astraToken }
        });
        const data = await res.json();
        if (apiKeyInput && data.has_api_key) {
            apiKeyInput.placeholder = "API Key active. (Enter new key to change)";
        }
    } catch (e) {}
}

if (settingsBtn) {
    settingsBtn.addEventListener('click', () => {
        openSettingsModal();
    });
}

if (closeModalBtn) {
    closeModalBtn.addEventListener('click', () => {
        closeSettingsModal();
    });
}

window.addEventListener('click', (e) => {
    if (e.target === settingsModal) {
        closeSettingsModal();
    }
});

window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && settingsModal && !settingsModal.classList.contains('hidden')) {
        closeSettingsModal();
    }
});

// Settings Tab Buttons Click Listeners
document.querySelectorAll('.console-tabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tabId = btn.getAttribute('data-tab');
        if (tabId) switchSettingsTab(tabId);
    });
});

// MCP Header Badge Click -> Opens Settings Modal directly to MCP Tab
const mcpHeaderBadge = document.getElementById('mcpHeaderBadge');
if (mcpHeaderBadge) {
    mcpHeaderBadge.addEventListener('click', () => {
        openSettingsModal('mcp');
    });
}

// Mobile Drawer & Sidebar Navigation Handlers
function openMobileDrawer() {
    if (appSidebar) {
        appSidebar.classList.remove('collapsed');
        appSidebar.classList.add('drawer-open');
    }
    if (sidebarBackdrop) sidebarBackdrop.classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeMobileDrawer() {
    if (appSidebar) appSidebar.classList.remove('drawer-open');
    if (sidebarBackdrop) sidebarBackdrop.classList.remove('active');
    document.body.style.overflow = '';
}

function toggleSidebarCollapse() {
    if (!appSidebar) return;
    if (window.innerWidth < 900) {
        if (appSidebar.classList.contains('drawer-open')) {
            closeMobileDrawer();
        } else {
            openMobileDrawer();
        }
        return;
    }
    appSidebar.classList.remove('drawer-open');
    appSidebar.classList.toggle('collapsed');
    const isCollapsed = appSidebar.classList.contains('collapsed');
    try {
        localStorage.setItem('astra_sidebar_collapsed', isCollapsed ? 'true' : 'false');
    } catch (e) {}
}

function startNewChatSession() {
    // 1. Interrupt active audio/TTS playback immediately
    interruptPlayback(false);

    // 2. Abort active network requests if any
    if (activeChatAbortController) {
        try { activeChatAbortController.abort(); } catch (e) {}
        activeChatAbortController = null;
    }
    if (activeVoiceUploadAbortController) {
        try { activeVoiceUploadAbortController.abort(); } catch (e) {}
        activeVoiceUploadAbortController = null;
    }

    // 3. Stop recording/listening
    if (isRecordingAudio) {
        stopMobileRecording(true);
    }
    if (voiceState === 'listening' || isMicActive()) {
        stopListening(false);
    }

    // 4. Generate fresh session ID
    astraSessionId = 'sess_' + Math.random().toString(36).substring(2, 10);
    try {
        sessionStorage.setItem('astra_session_id', astraSessionId);
    } catch (e) {}

    // 5. Reset chat messages back to clean initial state
    if (chatMessages) {
        chatMessages.innerHTML = `
            <div class="chat-bubble assistant">
                <div class="bubble-header">
                    <span class="bubble-sender">Astra</span>
                    <span class="bubble-time">Ready</span>
                </div>
                <div class="bubble-content">
                    <p class="bubble-text" id="assistantReply">"Namaste! Bolo kya open karna hai?"</p>
                </div>
            </div>
        `;
    }

    // 6. Reset transcripts and status
    if (liveTranscript) liveTranscript.textContent = '';
    if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');
    if (statusText) statusText.textContent = "Tap to speak";

    setVoiceState('idle');
    setOrbState('idle');

    // 7. Close drawer if open on mobile
    closeMobileDrawer();
}

if (mobileDrawerBtn) {
    mobileDrawerBtn.addEventListener('click', toggleSidebarCollapse);
}
if (sidebarCloseBtn) {
    sidebarCloseBtn.addEventListener('click', closeMobileDrawer);
}
if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener('click', closeMobileDrawer);
}
if (sidebarCollapseBtn) {
    sidebarCollapseBtn.addEventListener('click', toggleSidebarCollapse);
}
if (newChatBtn) {
    newChatBtn.addEventListener('click', startNewChatSession);
}
if (sidebarSettingsBtn) {
    sidebarSettingsBtn.addEventListener('click', () => {
        closeMobileDrawer();
        openSettingsModal();
    });
}
if (omnibarToolsBtn) {
    omnibarToolsBtn.addEventListener('click', () => {
        if (promptDeck) {
            promptDeck.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            promptDeck.classList.add('highlight-pulse');
            setTimeout(() => promptDeck.classList.remove('highlight-pulse'), 1200);
        }
    });
}

// Quick action chips & prompt suggestions click to execute
document.querySelectorAll('.prompt-card, .session-item, .auto-quick-chip, .mcp-tool-chip').forEach(el => {
    el.addEventListener('click', () => {
        const cmd = el.getAttribute('data-cmd');
        if (cmd) {
            closeMobileDrawer();
            sendVoiceCommand(cmd);
        }
    });
});


if (saveKeyBtn) {
    saveKeyBtn.addEventListener('click', async () => {
        const key = apiKeyInput ? apiKeyInput.value.trim() : "";
        if (!key) {
            if (saveStatus) {
                saveStatus.style.color = "#ef4444";
                saveStatus.textContent = "Please enter an API key.";
            }
            return;
        }

        try {
            const res = await fetch('/api/save_key', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Astra-Token': astraToken
                },
                body: JSON.stringify({ api_key: key })
            });
            const data = await res.json();
            if (saveStatus) {
                saveStatus.style.color = data.success ? "#10b981" : "#ef4444";
                saveStatus.textContent = data.message;
            }
            if (data.success && apiKeyInput) {
                apiKeyInput.value = "";
                setTimeout(() => {
                    closeSettingsModal();
                }, 1500);
            }
        } catch (e) {
            if (saveStatus) {
                saveStatus.style.color = "#ef4444";
                saveStatus.textContent = "Failed to save key: " + e.message;
            }
        }
    });
}

// =====================================================================
// Mobile Background Quick-Talk & Web Push Subscriptions
// =====================================================================

function triggerVoiceInput() {
    console.log("[Astra] Triggering instant voice input (Quick-Launch / Shortcut)...");
    if (isAssistantSpeaking()) {
        interruptPlayback(false);
    }
    setOrbState('listening');
    startListening();
}

// Listen for messages from the Service Worker (e.g. persistent notification click)
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (event) => {
        if (event.data && event.data.type === 'TRIGGER_TALK') {
            console.log("[Astra SW Message] Received TRIGGER_TALK");
            triggerVoiceInput();
        }
    });
}

// Convert URL-safe base64 VAPID public key to Uint8Array for PushManager
function urlB64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

// Enable Web Push Notifications and persistent background Quick-Talk tray
async function enablePushAndQuickTalk() {
    if (!('Notification' in window) || !('serviceWorker' in navigator)) {
        alert("Push notifications are not supported on this browser.");
        return;
    }

    try {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            if (pushNotificationText) pushNotificationText.textContent = "Denied";
            alert("Notification permission is required for background reminder alerts and Quick-Talk.");
            return;
        }

        const reg = await navigator.serviceWorker.ready;
        
        // 1. Fetch public VAPID key
        const keyRes = await fetch('/api/push/vapid_public_key', {
            credentials: 'same-origin',
            headers: { 'X-Astra-Token': astraToken }
        });
        const keyData = await keyRes.json();
        
        if (keyData && keyData.public_key) {
            const applicationServerKey = urlB64ToUint8Array(keyData.public_key);
            let sub = await reg.pushManager.getSubscription();
            if (!sub) {
                sub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: applicationServerKey
                });
            }

            // 2. Register subscription on backend
            await fetch('/api/push/subscribe', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Astra-Token': astraToken
                },
                body: JSON.stringify({ subscription: sub.toJSON() })
            });
        }

        // 3. Request Service Worker to show sticky persistent "Tap to talk" notification
        if (reg.active) {
            reg.active.postMessage({ type: 'SHOW_QUICK_TALK_NOTIFICATION' });
        }

        if (pushNotificationBtn) pushNotificationBtn.classList.add('active');
        if (pushNotificationText) pushNotificationText.textContent = "Alerts: ON";
        if (statusText) statusText.textContent = "Quick-Talk notification active in notification shade.";
        console.log("[Push] Subscribed and Quick-Talk notification pinned.");
    } catch (err) {
        console.error("[Push] Registration error:", err);
        if (pushNotificationText) pushNotificationText.textContent = "Error";
    }
}

if (pushNotificationBtn) {
    pushNotificationBtn.addEventListener('click', () => {
        enablePushAndQuickTalk();
    });
}

// Check status on initial page load & initialize ambient listener
window.addEventListener('DOMContentLoaded', async () => {
    if (!recognition) {
        recognition = initSpeechRecognition();
    }

    // Check if launched via Quick-Launch Home-screen Shortcut (?action=talk or ?action=voice)
    const urlParams = new URLSearchParams(window.location.search);
    const quickAction = urlParams.get('action');
    if (quickAction === 'talk' || quickAction === 'voice') {
        console.log("[Astra] Opened via Quick-Launch Shortcut ?action=" + quickAction);
        setTimeout(() => triggerVoiceInput(), 600);
    }

    // Initialize Network & MCP status
    updateNetworkStatus();
    fetchAndRenderMCPStatus();

    // Initialize Wake-Word UI state and display label
    if (wakeWordToggleBtn) {
        if (wakeWordEnabled) {
            wakeWordToggleBtn.classList.add('active');
        } else {
            wakeWordToggleBtn.classList.remove('active');
        }
    }
    updateWakeWordUI();

    // Check if notification permission is already granted
    if ('Notification' in window && Notification.permission === 'granted') {
        if (pushNotificationBtn) pushNotificationBtn.classList.add('active');
        if (pushNotificationText) pushNotificationText.textContent = "Alerts: ON";
    }

    // Sync token from backend session if not present
    if (!astraToken) {
        try {
            const tokenRes = await fetch('/api/auth/token', { credentials: 'same-origin' });
            if (tokenRes.ok) {
                const tokenData = await tokenRes.json();
                if (tokenData && tokenData.token) {
                    astraToken = tokenData.token;
                    localStorage.setItem('astra_token', astraToken);
                }
            }
        } catch (e) {}
    }

    try {
        const res = await fetch('/api/status', {
            credentials: 'same-origin',
            headers: { 'X-Astra-Token': astraToken }
        });
        const data = await res.json();
        if (data && data.tunnel_url) {
            cachedTunnelUrl = data.tunnel_url;
            // If on mobile device via insecure HTTP, immediately offer one-tap switch to secure HTTPS
            const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
            const isSecure = window.isSecureContext || isLocalhost || window.location.protocol.includes('https');
            if (!isSecure && isMobileDevice() && statusText) {
                statusText.innerHTML = "🔒 <a href='" + cachedTunnelUrl + "' style='color:#60a5fa;text-decoration:underline;font-weight:600;'>Tap to enable Mobile Mic (Switch to HTTPS)</a>";
            }
        }
        if (statusText && !quickAction) {
            const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
            const isSecure = window.isSecureContext || isLocalhost || window.location.protocol.includes('https');
            if (!(!isSecure && isMobileDevice() && cachedTunnelUrl)) {
                statusText.textContent = "Tap to speak";
            }
        }
    } catch (e) {
        if (statusText && !quickAction) {
            statusText.textContent = "Tap to speak";
        }
    }

    // Ensure desktop sidebar is open by default so sidebar options (Recent History, MCP Tools, Automations) are visible
    try {
        if (window.innerWidth >= 900 && appSidebar) {
            appSidebar.classList.remove('collapsed');
            localStorage.setItem('astra_sidebar_collapsed', 'false');
        }
    } catch (e) {}

    // Auto-start ambient listening for "Hey Astra" only if wake-word is explicitly enabled (desktop only)
    if (wakeWordEnabled && !isMobileDevice() && !quickAction) {
        scheduleWakeWordRestart(800);
    }
});
