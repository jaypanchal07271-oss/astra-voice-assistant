import os
import re
import uuid
import asyncio
from pathlib import Path
from typing import Optional
import edge_tts
from config import AUDIO_DIR, VOICE_HINDI, VOICE_ENGLISH, TTS_RATE, TTS_PITCH
from core.logger import get_logger

logger = get_logger("astra.tts")

# Supported Neural Voices
VOICES = {
    "hi": VOICE_HINDI,
    "en": VOICE_ENGLISH
}

# Compiled regex for stripping all Unicode emojis and pictographs
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FAFF"  # symbols and pictographs extended
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"
    "\U00002600-\U000026FF"  # miscellaneous symbols
    "\U00002B50-\U00002B55"
    "\U00002300-\U000023FF"
    "\u200d"                 # zero-width joiner
    "\ufe0f"                 # variation selector
    "\u2728-\u272f"
    "]+",
    flags=re.UNICODE
)


def clean_text_for_tts(text: str) -> str:
    """
    Sanitizes raw AI and rule responses for natural, human-like neural TTS:
    - Strips code blocks, backticks, bold/italic asterisks, hashes, markdown links, and URLs.
    - Removes all emojis and decorative symbols that cause robotic pauses or stuttering.
    - Normalizes awkward punctuation (ellipses, consecutive hyphens/dashes) into smooth breath pauses.
    - Ensures complete, natural sentence flow without fragmented chunks.
    """
    if not text or not text.strip():
        return "Kaam poora ho gaya hai."

    s = text.strip()

    # 1. Remove multi-line code blocks ```...``` entirely, and inline code ticks `code` -> code
    s = re.sub(r'```[\s\S]*?```', '', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)

    # 2. Markdown links [text](url) -> text, and strip standalone URLs
    s = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', s)
    s = re.sub(r'https?://\S+', '', s)

    # 3. Strip markdown headers (#, ##, ###) at start of lines
    s = re.sub(r'^\s*#+\s*', '', s, flags=re.MULTILINE)

    # 4. Remove bullet points and blockquote symbols (> , * , - ) at line start
    s = re.sub(r'^\s*[-*•>]\s+', '', s, flags=re.MULTILINE)

    # 5. Remove bold/italic markers (*, **, _, __, ~~)
    s = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', s)
    s = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', s)
    s = re.sub(r'~{1,2}([^~]+)~{1,2}', r'\1', s)
    s = re.sub(r'[*_~#|]', ' ', s)

    # 6. Remove all Unicode Emojis & Pictographs
    s = _EMOJI_PATTERN.sub('', s)

    # Strip decorative symbols often present in UI responses
    s = re.sub(r'[✦★☆●◆■▲▶◀⚙🎙🔊📝📸👁📋🚀🎧🎵⛩️✓✗✔✕]', '', s)

    # 7. Normalize awkward punctuation into natural speech pauses
    # Ellipses (..., …) -> comma for natural breathing pause
    s = re.sub(r'\.{2,}', ', ', s)
    s = re.sub(r'…', ', ', s)

    # Multiple hyphens or dashes (--, ---, ——) -> comma
    s = re.sub(r'[-–—]{2,}', ', ', s)
    s = re.sub(r'\s+[-–—]\s+', ', ', s)

    # Collapse repetitive punctuation marks
    s = re.sub(r'!+', '!', s)
    s = re.sub(r'\?+', '?', s)
    s = re.sub(r',+', ',', s)

    # Ensure no awkward space before punctuation
    s = re.sub(r'\s+([,.:;?!])', r'\1', s)

    # Ensure space after punctuation when missing without breaking file extensions (e.g. notepad.exe)
    s = re.sub(r'([,;:])(?=[^\s])', r'\1 ', s)
    s = re.sub(r'([.!?])(?=[A-Z\u0900-\u097F])', r'\1 ', s)

    # Collapse multiple whitespaces and newlines into single spaces
    s = re.sub(r'\s+', ' ', s).strip()

    if not s:
        return "Kaam poora ho gaya hai."

    return s


def detect_lang_from_text(text: str) -> str:
    """Detects Hindi (Devanagari or common Hinglish keywords) vs English."""
    if re.search(r'[\u0900-\u097F]', text):
        return "hi"
    hindi_words = {
        "hai", "hain", "karo", "khol", "kholo", "diya", "raha", "rahe", "aap", "kya",
        "shukriya", "dhanyawad", "namaste", "samay", "tareekh", "baje", "badha", "badhao",
        "kar", "ho", "gaya", "bhai", "chalao", "sunao", "karna", "nahi", "karte", "denge",
        "lijiye", "boliye", "bataiye", "badhiya", "theek", "kaise", "kaisa", "maine", "hukum"
    }
    words = re.findall(r'\b\w+\b', text.lower())
    matches = sum(1 for w in words if w in hindi_words)
    return "hi" if matches >= 1 else "en"


async def generate_audio(text: str, lang: str = "hi-IN", force_lang: str = "") -> str:
    """
    Generates an MP3 audio file from text using edge-tts.
    - Sanitizes text completely to eliminate robotic artifacting and pauses.
    - Processes complete response / full sentences to maintain natural prosody.
    - Tunes speech speed (rate) and pitch for an energetic, fluid conversational flow.
    - Verifies file integrity before returning URL to prevent buffer stuttering.
    """
    clean_text = clean_text_for_tts(text)

    # Determine voice based on explicit lang parameter, force_lang, or intelligent detection
    chosen_lang = (force_lang or lang or "").lower().strip()
    if "hi" in chosen_lang or "hindi" in chosen_lang:
        voice = VOICES["hi"]
    elif "en" in chosen_lang or "english" in chosen_lang:
        voice = VOICES["en"]
    else:
        detected = detect_lang_from_text(clean_text)
        voice = VOICES.get(detected, VOICES["hi"])

    # Ensure audio directory exists
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    unique_id = uuid.uuid4().hex[:8]
    filename = f"speech_{unique_id}.mp3"
    filepath = AUDIO_DIR / filename

    rate = os.getenv("TTS_RATE", TTS_RATE)
    pitch = os.getenv("TTS_PITCH", TTS_PITCH)

    # Generate complete natural speech with tuned rate and pitch
    communicate = edge_tts.Communicate(
        clean_text,
        voice,
        rate=rate,
        pitch=pitch,
        boundary="SentenceBoundary"
    )
    await communicate.save(str(filepath))

    # Fast audio playback verification: ensure file exists and is non-empty
    if not filepath.exists() or filepath.stat().st_size == 0:
        raise RuntimeError(f"Generated TTS file {filename} is missing or empty.")

    # Clean up older files (keep latest 20)
    try:
        audio_files = sorted(AUDIO_DIR.glob("speech_*.mp3"), key=os.path.getmtime)
        if len(audio_files) > 20:
            for old in audio_files[:-20]:
                try:
                    old.unlink()
                except Exception:
                    pass
    except Exception:
        pass

    return f"/static/audio/{filename}"


# Backward compatibility alias
generate_speech_audio = generate_audio
