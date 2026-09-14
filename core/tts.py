import os
import re
import uuid
import asyncio
from pathlib import Path
from typing import Optional
import edge_tts
from config import AUDIO_DIR, VOICE_HINDI, VOICE_ENGLISH, TTS_RATE, TTS_PITCH, TTS_TIMEOUT
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
    - Strips quotation marks and outer brackets/parentheses to eliminate quotation/aside pauses.
    - Converts non-time colons and semicolons to natural conversational breathing pauses (soft commas).
    - Normalizes ellipses and dashes into smooth breath pauses without leaving unfinished trailing commas.
    - Preserves Hindi Devanagari, Hinglish, English, digital times (10:30), numbers, and technical terms.
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

    # 4. Remove bullet points (-, *, •, >) and numbered list headers (1. , 2) ) at line starts
    s = re.sub(r'^\s*[-*•>]\s+', '', s, flags=re.MULTILINE)
    s = re.sub(r'^\s*\d+[\.\)]\s*', '', s, flags=re.MULTILINE)
    s = re.sub(r'(?<=[.!?\s])\d+[\.\)]\s+(?=[A-Za-z\u0900-\u097F])', '', s)

    # 4b. Convert line breaks into sentence pauses before collapsing whitespace
    s = re.sub(r'[\r\n]+', '. ', s)

    # 5. Remove bold/italic markers (*, **, _, __, ~~)
    s = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', s)
    s = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', s)
    s = re.sub(r'~{1,2}([^~]+)~{1,2}', r'\1', s)
    s = re.sub(r'[*_~#|]', ' ', s)

    # 6. Remove all Unicode Emojis & Pictographs
    s = _EMOJI_PATTERN.sub('', s)

    # Strip decorative symbols often present in UI responses
    s = re.sub(r'[✦★☆●◆■▲▶◀⚙🎙🔊📝📸👁📋🚀🎧🎵⛩️✓✗✔✕]', '', s)

    # 7. Remove outer brackets and parentheses while keeping inner words
    s = re.sub(r'\(([^)]+)\)', r' \1 ', s)
    s = re.sub(r'\[([^\]]+)\]', r' \1 ', s)
    s = re.sub(r'\{([^}]+)\}', r' \1 ', s)

    # 8. Strip quotation marks (keep apostrophes in English contractions like don't, it's)
    s = re.sub(r'["“”«»„]', '', s)
    s = re.sub(r'(?:^|\s)[\'‘]+', ' ', s)
    s = re.sub(r'[\'’]+(?:$|[\s,.:;?!])', ' ', s)

    # 9. Normalize awkward colons & semicolons
    # Semicolons -> soft comma
    s = re.sub(r';', ', ', s)
    # Colons not surrounded by digits (preserves 10:30, 12:45) -> soft comma
    s = re.sub(r'(?<!\d):|:(?!\d)', ', ', s)

    # 10. Normalize ellipses and dashes into natural breath pauses
    s = re.sub(r'\.{2,}', ', ', s)
    s = re.sub(r'…', ', ', s)
    s = re.sub(r'[-–—]{2,}', ', ', s)
    s = re.sub(r'\s+[-–—]\s+', ', ', s)

    # 11. Eliminate conflicting punctuation combinations (e.g. ,. -> . or ., -> .)
    s = re.sub(r'[,;:]+\s*[.!?]', '.', s)
    s = re.sub(r'[.!?]+\s*[,;:]', '.', s)

    # 12. Collapse repetitive punctuation marks
    s = re.sub(r'!+', '!', s)
    s = re.sub(r'\?+', '?', s)
    s = re.sub(r',+', ',', s)
    s = re.sub(r'\.+', '.', s)

    # 13. Spacing rules around punctuation
    # No space before punctuation
    s = re.sub(r'\s+([,.:;?!])', r'\1', s)
    # Ensure space after punctuation when missing (except decimal numbers like 3.5)
    s = re.sub(r'([,])(?=[^\s])', r'\1 ', s)
    s = re.sub(r'([.!?])(?=[A-Z\u0900-\u097F])', r'\1 ', s)

    # 13b. Insert natural conversational breath pause after introductory acknowledgment phrases
    s = re.sub(
        r'\b(Bilkul|Zaroor|Haanji|Ji Boss|Sure|Certainly|Of course|Shukriya|Dhanyawad)\b(?:\s+(Boss|Jay|Sir))?\s*(?![,.!?])',
        r'\g<0>, ',
        s,
        flags=re.IGNORECASE
    )

    # 14. Collapse multiple whitespaces and newlines into single spaces
    s = re.sub(r'\s+', ' ', s).strip()

    # 15. Clean leading & trailing punctuation for smooth terminal cadence & rising question inflection
    s = re.sub(r'^[,;:\-–—\s]+', '', s)
    s = re.sub(r'[,;:\-–—\s]+$', '', s)

    # Detect trailing inquiry clauses to ensure '?' is preserved for natural rising pitch inflection
    is_question = bool(re.search(
        r'(?:\b(kya|kaise|kaisa|kyun|kab|kaha|kaun|bataoon|chahiye|karein|would you|could you|can I|how can|is there|shall I)\b[^.!?]*$|\?$)',
        s,
        re.IGNORECASE
    ))

    if s and s[-1] not in '.!?।':
        s += '?' if is_question else '.'
    elif s and s[-1] == '.' and is_question:
        s = s[:-1] + '?'

    if not s or s in ('.', '।', '?'):
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

    logger.info(
        "TTS synthesis initiated",
        extra={
            "tts_input_length": len(text),
            "clean_text_length": len(clean_text),
            "tts_input_preview": clean_text[:120],
            "voice": voice,
            "rate": rate,
            "pitch": pitch
        }
    )

    # Generate complete natural speech with tuned rate and pitch
    communicate = edge_tts.Communicate(
        clean_text,
        voice,
        rate=rate,
        pitch=pitch,
        boundary="SentenceBoundary"
    )
    try:
        raw_to = os.getenv("TTS_TIMEOUT")
        tts_timeout = float(raw_to) if raw_to is not None else float(TTS_TIMEOUT)
    except (ValueError, TypeError):
        tts_timeout = float(TTS_TIMEOUT)
    try:
        await asyncio.wait_for(communicate.save(str(filepath)), timeout=tts_timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        logger.warning(f"TTS synthesis timed out or cancelled after {tts_timeout}s for voice '{voice}'")
        if filepath.exists():
            try:
                filepath.unlink()
            except Exception:
                pass
        raise TimeoutError(f"TTS synthesis timed out after {tts_timeout}s")
    except Exception as save_err:
        if filepath.exists():
            try:
                filepath.unlink()
            except Exception:
                pass
        raise save_err

    # Fast audio playback verification: ensure file exists and is non-empty
    if not filepath.exists() or filepath.stat().st_size == 0:
        raise RuntimeError(f"Generated TTS file {filename} is missing or empty.")

    audio_url = f"/static/audio/{filename}"
    logger.info(
        "TTS synthesis complete",
        extra={
            "generated_audio_url": audio_url,
            "file_size_bytes": filepath.stat().st_size
        }
    )

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
