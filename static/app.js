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

// Mobile Audio Autoplay Policy Unlocker (Unlocks HTML5 audio on first touch/tap)
let audioUnlocked = false;
function unlockMobileAudio() {
    if (audioUnlocked) return;
    if (audioPlayer) {
        audioPlayer.play().then(() => {
            audioPlayer.pause();
            audioPlayer.currentTime = 0;
            audioUnlocked = true;
            console.log("[Audio] Mobile audio unlocked for playback.");
        }).catch(() => {});
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

// State Variables
let isListening = false;
let currentLang = 'hi-IN'; // default to Hindi/Hinglish (hi-IN)
let recognition = null;
let lastProcessedTranscript = "";
let isProcessing = false;
let wakeWordEnabled = true; // Ambient Wake-Word mode
let wakeWordActive = false;

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

// MediaRecorder Fallback State (for iOS Safari and unsupported browsers)
let mediaRecorder = null;
let audioChunks = [];
let isRecordingAudio = false;

// 1. Initialize Web Speech API
function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        if (statusText) statusText.textContent = "Astra Ready • Click Mic to Record & Talk";
        console.log("[Mic] SpeechRecognition not available; MediaRecorder fallback active.");
        return null;
    }

    const rec = new SpeechRecognition();
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.lang = currentLang;

    rec.onstart = () => {
        isListening = true;
        isProcessing = false;
        lastProcessedTranscript = "";
        if (micBtn) micBtn.classList.add('active');
        setOrbState('listening');
        if (statusText) statusText.textContent = "Sun raha hoon... Kahiye!";
        if (liveTranscript) liveTranscript.textContent = "";
        console.log("[Mic] Listening started with language:", rec.lang);
    };

    rec.onspeechstart = () => {
        // Instant Barge-In: If user starts speaking while audio is playing, kill audio immediately
        if (isAssistantSpeaking()) {
            console.log("[Astra] User voice detected during playback. Interrupting immediately.");
            interruptPlayback(false);
            isListening = true;
            setOrbState('listening');
            if (statusText) statusText.textContent = "Sun raha hoon... (Interrupted)";
        }
    };

    rec.onresult = (event) => {
        let interimText = '';
        let finalText = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
            const transcript = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
                finalText += transcript;
            } else {
                interimText += transcript;
            }
        }

        const currentText = (finalText || interimText).trim();

        // 1. Barge-In Interruption check
        if (currentText.length > 0 && isAssistantSpeaking()) {
            console.log("[Astra] Voice Barge-in. User spoke:", currentText);
            interruptPlayback(false);
            isListening = true;
            setOrbState('listening');
            if (statusText) statusText.textContent = "Sun raha hoon... (Interrupted)";
        }

        // 2. Ambient Wake Word Detection ("Hey Astra" / "Hey Jarvis" / "Astra" / "Jarvis")
        if (wakeWordEnabled && !isProcessing) {
            const wakeRegex = getWakeWordRegex();
            if (wakeRegex.test(currentText)) {
                // Strip wake word and check if a command followed immediately
                const cleanedCommand = currentText.replace(wakeRegex, '').replace(/^[,.:\s-]+/, '').trim();
                const matchedName = getWakeWordDisplayName();
                console.log(`[${matchedName}] Wake word triggered! Remaining command:`, cleanedCommand);

                // Visual wake pulse
                if (orbWrapper) {
                    orbWrapper.classList.add('listening');
                }

                if (cleanedCommand.length > 2 && finalText) {
                    // Command came in the same breath: execute directly!
                    lastProcessedTranscript = finalText.trim();
                    isProcessing = true;
                    stopListening();
                    sendVoiceCommand(cleanedCommand);
                    return;
                } else if (!wakeWordActive) {
                    wakeWordActive = true;
                    if (statusText) statusText.textContent = `${matchedName} active! Kahiye kya command hai?`;
                    if (liveTranscript) liveTranscript.textContent = `✦ ${matchedName} Listening...`;
                    return;
                }
            }
        }

        if (liveTranscript) {
            liveTranscript.textContent = currentText;
        }
        if (liveTranscriptWrapper) {
            if (currentText.length > 0) {
                liveTranscriptWrapper.classList.add('active');
            } else {
                liveTranscriptWrapper.classList.remove('active');
            }
        }

        // 3. Finalized voice command dispatch
        if (finalText && finalText.trim().length > 0 && !isProcessing) {
            const cleanFinal = finalText.trim();
            if (cleanFinal !== lastProcessedTranscript) {
                // If wake word was primed, strip it from command
                const stripRegex = getWakeWordStripRegex();
                const stripped = cleanFinal.replace(stripRegex, '').trim();
                const toSend = stripped || cleanFinal;

                lastProcessedTranscript = cleanFinal;
                wakeWordActive = false;
                isProcessing = true;
                stopListening();
                if (liveTranscript) liveTranscript.textContent = '';
                if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');
                sendVoiceCommand(toSend);
            }
        }
    };

    rec.onerror = (event) => {
        console.warn("[Mic] Speech recognition notice:", event.error);
        if (event.error === 'not-allowed') {
            if (statusText) statusText.textContent = "Microphone access blocked. Address bar me Mic allow karein.";
            alert("Microphone permission blocked! Please allow microphone access in your browser address bar.");
            stopListening();
        } else if (event.error === 'no-speech') {
            // In continuous ambient mode, restart quietly
            if (wakeWordEnabled && !isProcessing && !isAssistantSpeaking()) {
                // Keep listening quietly for wake word
            }
        }
    };

    rec.onend = () => {
        console.log("[Mic] Speech recognition ended.");
        // If Wake Word ambient mode is active, automatically restart listening
        if (wakeWordEnabled && !isProcessing && !isAssistantSpeaking()) {
            setTimeout(() => {
                try {
                    if (rec && !isProcessing && !isAssistantSpeaking()) {
                        rec.start();
                    }
                } catch (e) {}
            }, 300);
        } else if (isListening) {
            stopListening();
        }
    };

    return rec;
}

// // 2. State & Orb Animations Controller (Supports .orb-listening, .orb-thinking, .orb-speaking, .orb-idle)
function setOrbState(state) {
    if (!orbWrapper) return;
    orbWrapper.classList.remove(
        'listening', 'thinking', 'speaking', 'idle',
        'orb-listening', 'orb-thinking', 'orb-speaking', 'orb-idle'
    );
    if (state !== 'idle') {
        orbWrapper.classList.add(state);
        orbWrapper.classList.add(`orb-${state}`);
    } else {
        orbWrapper.classList.add('orb-idle');
    }

    if (state === 'idle') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-cyan)";
        if (statusText) {
            statusText.textContent = wakeWordEnabled
                ? "Astra Ready • Say 'Hey Astra' or Click Mic"
                : "Astra Ready • Click Mic or Press Spacebar";
        }
    } else if (state === 'listening') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-emerald)";
        if (statusText) statusText.textContent = "Listening... Kahiye!";
    } else if (state === 'thinking') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-purple)";
        if (statusText) statusText.textContent = "Astra is thinking & executing...";
    } else if (state === 'speaking') {
        if (statusDot) statusDot.style.backgroundColor = "var(--accent-blue)";
        if (statusText) statusText.textContent = "Speaking... (Space, Esc ya bolkar interrupt karein)";
    }
}

// Dynamically Append Messages to Multi-Line Chat History with Smooth Auto-Scroll
function appendChatMessage(role, text) {
    if (!chatMessages) return;

    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${role}`;

    const sender = document.createElement('span');
    sender.className = 'bubble-sender';
    sender.textContent = role === 'user' ? 'You' : 'Astra';

    const content = document.createElement('p');
    content.className = 'bubble-text';
    content.textContent = text;

    bubble.appendChild(sender);
    bubble.appendChild(content);
    chatMessages.appendChild(bubble);

    // Smooth auto-scroll to newest message at the bottom
    chatMessages.scrollTo({
        top: chatMessages.scrollHeight,
        behavior: 'smooth'
    });
}

// =====================================================================
// MediaRecorder Audio Recording & Upload Fallback (iOS Safari / Mobile PWA)
// =====================================================================

async function startMediaRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert("Audio recording is not supported on this browser/environment. Please use text input.");
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        
        // Choose supported mimeType for recording
        let options = {};
        if (typeof MediaRecorder !== 'undefined') {
            if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                options = { mimeType: 'audio/webm;codecs=opus' };
            } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
                options = { mimeType: 'audio/mp4' };
            } else if (MediaRecorder.isTypeSupported('audio/wav')) {
                options = { mimeType: 'audio/wav' };
            }
        }

        mediaRecorder = new MediaRecorder(stream, options);

        mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        mediaRecorder.onstop = async () => {
            // Stop mic hardware track
            stream.getTracks().forEach(track => track.stop());

            if (audioChunks.length === 0) {
                setOrbState('idle');
                return;
            }

            const mimeType = mediaRecorder.mimeType || 'audio/webm';
            const audioBlob = new Blob(audioChunks, { type: mimeType });
            await sendVoiceUpload(audioBlob, mimeType);
        };

        mediaRecorder.start();
        isRecordingAudio = true;
        isListening = true;
        if (micBtn) micBtn.classList.add('active');
        setOrbState('listening');
        if (statusText) statusText.textContent = "Recording voice... Click Mic again to Send";
        console.log("[MediaRecorder] Started audio recording fallback:", options);

    } catch (err) {
        console.error("[MediaRecorder] Recording error:", err);
        if (statusText) statusText.textContent = "Mic access blocked: " + err.message;
        setOrbState('idle');
    }
}

function stopMediaRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }
    isRecordingAudio = false;
    isListening = false;
    if (micBtn) micBtn.classList.remove('active');
}

async function sendVoiceUpload(audioBlob, mimeType) {
    setOrbState('thinking');
    if (statusText) statusText.textContent = "Astra is transcribing & executing...";

    const formData = new FormData();
    const ext = mimeType.includes('mp4') ? 'mp4' : (mimeType.includes('wav') ? 'wav' : 'webm');
    formData.append('audio_file', audioBlob, `recording.${ext}`);
    formData.append('session_id', astraSessionId);
    formData.append('lang', currentLang);

    try {
        const response = await fetch('/api/voice_upload', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-Astra-Token': astraToken
            },
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Server returned HTTP ${response.status}`);
        }

        const data = await response.json();
        console.log("[Astra] Voice upload result:", data);

        if (data.transcript) {
            appendChatMessage('user', data.transcript);
        }
        appendChatMessage('assistant', data.reply);
        if (assistantReply) {
            assistantReply.textContent = `"${data.reply}"`;
        }

        if (data.audio_url) {
            playAudioResponse(data.audio_url);
        } else {
            setOrbState('idle');
            isProcessing = false;
        }

    } catch (err) {
        console.error("[Astra] Voice upload error:", err);
        appendChatMessage('assistant', `Note: ${err.message}`);
        setOrbState('idle');
        isProcessing = false;
    }
}

function startListening() {
    // Check if on mobile over insecure HTTP (non-localhost, non-https)
    const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    const isSecure = window.isSecureContext || isLocalhost || window.location.protocol.includes('https');
    const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent) || window.innerWidth <= 768;

    if (isMobile && !isSecure) {
        console.warn("[Mic] Mobile mic requires HTTPS or Chrome flag on LAN origin:", window.location.origin);
        if (statusText) statusText.textContent = "Mic requires HTTPS on phone • Type command below";
        if (textCommandInput) {
            textCommandInput.focus();
            textCommandInput.placeholder = "Type your command here (Text mode active)...";
        }
        return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        // Fall back to MediaRecorder upload for mobile browsers without Web Speech API
        startMediaRecording();
        return;
    }

    if (!recognition) {
        recognition = initSpeechRecognition();
    }
    if (!recognition) {
        startMediaRecording();
        return;
    }

    try {
        recognition.lang = currentLang;
        if (audioPlayer) {
            audioPlayer.pause();
            audioPlayer.currentTime = 0;
        }
        updateInterruptUI(false);
        isProcessing = false;
        recognition.start();
    } catch (err) {
        console.warn("[Mic] recognition.start note:", err);
        // Fallback to MediaRecorder if recognition start fails
        startMediaRecording();
    }
}

function stopListening() {
    if (isRecordingAudio) {
        stopMediaRecording();
        return;
    }

    isListening = false;
    if (micBtn) micBtn.classList.remove('active');
    if (recognition) {
        try {
            recognition.stop();
        } catch (e) {}
    }
    if (orbWrapper && !orbWrapper.classList.contains('thinking') && !orbWrapper.classList.contains('speaking') && !orbWrapper.classList.contains('orb-thinking') && !orbWrapper.classList.contains('orb-speaking')) {
        setOrbState('idle');
    }
}

// 3. Integration Layer: Send voice/text command to FastAPI (Authenticated & Multi-Turn Memory)
async function sendVoiceCommand(commandText) {
    if (!commandText || !commandText.trim()) {
        isProcessing = false;
        setOrbState('idle');
        return;
    }

    const cleanCommand = commandText.trim();
    console.log("[Astra] Sending command to backend:", cleanCommand, "Session:", astraSessionId);

    // Dynamically append user message to chat history & clear live interim transcript
    appendChatMessage('user', cleanCommand);
    if (liveTranscript) liveTranscript.textContent = '';
    if (liveTranscriptWrapper) liveTranscriptWrapper.classList.remove('active');

    setOrbState('thinking');
    if (statusText) statusText.textContent = "Astra thinking & executing tool...";

    try {
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
                session_id: astraSessionId
            })
        });

        if (!response.ok) {
            if (response.status === 401) {
                throw new Error("Authentication failed: Invalid security token.");
            }
            throw new Error(`Server returned HTTP ${response.status}`);
        }

        const data = await response.json();
        console.log("[Astra] Received response from backend:", data);

        // Dynamically append assistant response to chat history
        appendChatMessage('assistant', data.reply);
        if (assistantReply) {
            assistantReply.textContent = `"${data.reply}"`;
        }

        // Play generated Edge-TTS audio
        if (data.audio_url) {
            playAudioResponse(data.audio_url);
        } else {
            setOrbState('idle');
            isProcessing = false;
            if (wakeWordEnabled) startListening();
        }

    } catch (error) {
        console.error("[Astra] Backend communication error:", error);
        appendChatMessage('assistant', `Note: ${error.message}`);
        if (assistantReply) {
            assistantReply.textContent = `Backend note: ${error.message}`;
        }
        setOrbState('idle');
        isProcessing = false;
        if (wakeWordEnabled) startListening();
    }
}

// Helper to check if assistant audio is currently playing
function isAssistantSpeaking() {
    return audioPlayer && !audioPlayer.paused && audioPlayer.currentTime > 0 && !audioPlayer.ended;
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
    if (audioPlayer) {
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
    }
    updateInterruptUI(false);
    isProcessing = false;

    if (startListeningNow) {
        startListening();
    } else {
        stopListening();
        setOrbState('idle');
        if (statusText) statusText.textContent = "Interrupted • Click Mic or Press Spacebar";
        if (wakeWordEnabled) startListening();
    }
}

// 4. Play Audio Response using HTML5 Audio (Buffers fully to prevent stutter)
function playAudioResponse(audioUrl) {
    try {
        const fullUrl = audioUrl + `?t=${Date.now()}`;
        audioPlayer.pause();
        audioPlayer.currentTime = 0;
        audioPlayer.preload = "auto";
        audioPlayer.src = fullUrl;
        audioPlayer.load();

        audioPlayer.onplay = () => {
            setOrbState('speaking');
            updateInterruptUI(true);
            if (statusText) statusText.textContent = "Astra bol raha hai... (Space, Esc ya bolkar interrupt karein)";
            console.log("[TTS] Audio playback started.");

            // Start background voice barge-in recognition
            try {
                if (recognition && !isListening) {
                    recognition.start();
                }
            } catch (e) {}
        };

        audioPlayer.onended = () => {
            console.log("[TTS] Audio playback finished.");
            updateInterruptUI(false);
            setOrbState('idle');
            isProcessing = false;
            // Resume ambient wake-word listening
            if (wakeWordEnabled) {
                startListening();
            }
        };

        audioPlayer.onpause = () => {
            updateInterruptUI(false);
        };

        audioPlayer.onerror = (err) => {
            console.warn("[TTS] Audio playback error:", err);
            updateInterruptUI(false);
            setOrbState('idle');
            isProcessing = false;
            if (wakeWordEnabled) startListening();
        };

        let playbackStarted = false;
        const startPlayback = () => {
            if (playbackStarted) return;
            playbackStarted = true;
            audioPlayer.removeEventListener('canplaythrough', startPlayback);
            audioPlayer.removeEventListener('canplay', startPlayback);

            const playPromise = audioPlayer.play();
            if (playPromise !== undefined) {
                playPromise.catch(error => {
                    console.warn("[TTS] Autoplay blocked by browser policy:", error);
                    updateInterruptUI(false);
                    setOrbState('idle');
                    isProcessing = false;
                    appendAudioTapPrompt(audioPlayer);
                    if (wakeWordEnabled) startListening();
                });
            }
        };

        // Listen for buffer readiness before beginning playback
        audioPlayer.addEventListener('canplaythrough', startPlayback, { once: true });
        audioPlayer.addEventListener('canplay', startPlayback, { once: true });

        // Fallback safety timeout if canplay event was already dispatched or delayed
        setTimeout(() => {
            if (!playbackStarted && audioPlayer.readyState >= 2) {
                startPlayback();
            }
        }, 200);

    } catch (err) {
        console.error("[TTS] Audio player initialization error:", err);
        updateInterruptUI(false);
        setOrbState('idle');
        isProcessing = false;
    }
}

// Append quick tap-to-listen button if mobile browser restricts async audio autoplay
function appendAudioTapPrompt(player) {
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

if (micBtn) {
    micBtn.addEventListener('click', () => {
        if (isAssistantSpeaking() || isProcessing) {
            interruptPlayback(true);
        } else if (isListening) {
            stopListening();
        } else {
            startListening();
        }
    });
}

// Click on Orb to talk or interrupt
if (orbWrapper) {
    orbWrapper.addEventListener('click', () => {
        if (isAssistantSpeaking() || isProcessing) {
            interruptPlayback(true);
        } else if (isListening) {
            stopListening();
        } else {
            startListening();
        }
    });
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
            interruptPlayback(true);
        } else if (!isListening) {
            startListening();
        }
    }
});

window.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && e.target.tagName !== 'INPUT') {
        e.preventDefault();
        if (isListening && !wakeWordEnabled) {
            setTimeout(() => stopListening(), 400);
        }
    }
});

// Wake Word Toggle Button Handler
if (wakeWordToggleBtn) {
    wakeWordToggleBtn.addEventListener('click', () => {
        wakeWordEnabled = !wakeWordEnabled;
        if (wakeWordEnabled) {
            wakeWordToggleBtn.classList.add('active');
            startListening();
            console.log("[Astra] Ambient Wake-Word mode enabled.");
        } else {
            wakeWordToggleBtn.classList.remove('active');
            stopListening();
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

// Settings Modal & API Key Management (Authenticated)
if (settingsBtn) {
    settingsBtn.addEventListener('click', async () => {
        if (settingsModal) settingsModal.classList.add('open');
        if (saveStatus) saveStatus.textContent = "";
        if (wakeWordSelect) wakeWordSelect.value = wakeWordPreference;
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
    });
}

if (closeModalBtn) {
    closeModalBtn.addEventListener('click', () => {
        if (settingsModal) settingsModal.classList.remove('open');
    });
}

window.addEventListener('click', (e) => {
    if (e.target === settingsModal) {
        settingsModal.classList.remove('open');
    }
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
                    if (settingsModal) settingsModal.classList.remove('open');
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

    // Initialize Wake-Word UI state and display label
    if (wakeWordToggleBtn) {
        wakeWordToggleBtn.classList.add('active');
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
        if (statusText && !quickAction) {
            const name = getWakeWordDisplayName();
            statusText.textContent = `Astra Ready • Say 'Hey ${name}' or Click Mic`;
        }
    } catch (e) {
        if (statusText && !quickAction) {
            const name = getWakeWordDisplayName();
            statusText.textContent = `Astra Ready • Say 'Hey ${name}' or Click Mic`;
        }
    }

    // Auto-start ambient listening for "Hey Astra" if not already triggered by shortcut
    if (wakeWordEnabled && !quickAction) {
        setTimeout(() => startListening(), 800);
    }
});
