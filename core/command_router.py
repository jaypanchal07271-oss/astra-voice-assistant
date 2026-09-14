"""
Astra Assistant - Priority-Based Command Router
Provides deterministic intent classification, conjunction handling, parameter extraction,
and strict intent isolation.

Routing Hierarchy:
1. Explicit WhatsApp commands (Highest Priority, Terminal Route)
2. Explicit Windows Settings commands (WiFi, Bluetooth, Display, Sound, General)
3. Explicit Application launch / close (Chrome, Notepad, Calculator, VS Code, etc.)
4. Media & Automations (YouTube, Spotify, Instagram)
5. Explicit Web search (search_web_for_answer)
6. General / Fallback conversation
"""

import re
from typing import Dict, Any, Optional, Tuple, List
from core.logger import get_logger

logger = get_logger("astra.router")

# Conjunction words used to join compound clauses - should NOT be treated as contact names
CONJUNCTIONS = {"and", "aur", "then", "phir", "fir"}

# Words that should never be considered valid WhatsApp contact names
NON_CONTACT_WORDS = {
    "a", "the", "to", "someone", "unread", "chat", "message", "msg",
    "par", "pe", "ko", "me", "mein", "hi", "ki", "ke", "open", "web", "kholo",
    "karo", "chalao", "start", "launch", "send", "bolo", "reply", "say", "saying",
    "and", "aur", "then", "phir", "fir", "please", "zara", "jaldi", "yaar", "bhai",
    "bhejo", "bhej", "do", "likho", "send karo", "bhej do"
}

# Action filler words in messages
ACTION_FILLERS = {
    "bhejo", "karo", "do", "karna hai", "send karo", "please", "par message",
    "message", "msg", "say", "saying", "send", "bhej do", "send kar do", ""
}


# Comprehensive Indic/Devanagari to Latin Transliteration Mapping
DEV_PHRASES = [
    ("व्हाट्स एप", "whatsapp"),
    ("वाट्स एप", "whatsapp"),
    ("गूगल क्रोम", "chrome"),
    ("गुगल क्रोम", "chrome"),
    ("बंद करो", "close"),
    ("शुरू करो", "start"),
    ("चालू करो", "start"),
    ("सर्च करो", "search"),
    ("टाइप करो", "type"),
    ("लॉक करो", "lock"),
    ("मैसेज भेजो", "message bhejo"),
    ("भेज दो", "bhej do"),
    ("कर दो", "kar do"),
    ("वीएस कोड", "vscode"),
    ("स्क्रीन देखो", "screen dekho"),
    ("आवाज बढ़ाओ", "volume up"),
    ("आवाज कम करो", "volume down"),
]

DEV_TO_LATIN = {
    # Apps & Platforms
    "व्हाट्सएप": "whatsapp", "वाट्सएप": "whatsapp", "व्हाट्सऐप": "whatsapp", "वॉट्सएप": "whatsapp", "व्हाट्सअप": "whatsapp", "वाट्सअप": "whatsapp",
    "इंस्टाग्राम": "instagram", "इन्स्टाग्राम": "instagram", "इंस्टा": "instagram", "इन्स्टा": "instagram",
    "यूट्यूब": "youtube", "युट्यूब": "youtube", "वाईटी": "yt",
    "स्पॉटिफ़ाई": "spotify", "स्पॉटिफाई": "spotify", "स्पॉटीफाई": "spotify", "स्पॉटीफाइ": "spotify",
    "गूगल": "google", "गुगल": "google",
    "क्रोम": "chrome",
    "नोटपैड": "notepad", "नोटपेड": "notepad",
    "कैलकुलेटर": "calculator", "केलकुलेटर": "calculator",
    "कैमरा": "camera", "केमरा": "camera",
    "सेटिंग्स": "settings", "सेटिंग": "settings",
    "वाईफाई": "wifi", "वाइफाइ": "wifi",
    "ब्लूटूथ": "bluetooth", "ब्लुटूथ": "bluetooth",
    "ट्विटर": "twitter", "फेसबुक": "facebook", "टेलीग्राम": "telegram", "लिंक्डइन": "linkedin", "गिटहब": "github",
    "जीमेल": "gmail", "ईमेल": "email", "मेल": "mail", "कैलेंडर": "calendar",

    # Actions
    "ओपन": "open", "खोलो": "kholo", "खोल": "kholo", "खोलिए": "kholo", "खोलना": "kholo",
    "क्लोज": "close", "बंद": "close", "हटाओ": "close", "काटो": "close",
    "चलाओ": "chalao", "बजाओ": "chalao", "प्ले": "play", "सुनो": "suno", "सुनाओ": "sunao",
    "रोको": "stop", "पॉज": "pause", "स्टॉप": "stop",
    "सर्च": "search", "ढूंढो": "search", "ढूँढो": "search", "खोजो": "search",
    "मैसेज": "message", "संदेश": "message", "चैट": "chat",
    "भेजो": "bhejo", "भेज": "bhejo", "भेजिए": "bhejo", "सेंड": "send",
    "बोलो": "bolo", "कहो": "kaho", "बताओ": "batao",
    "टाइप": "type", "लिखो": "likho", "लिख": "likho",
    "लॉक": "lock", "स्क्रीन": "screen",
    "आवाज": "aawaz", "आवाज़": "aawaz", "वॉल्यूम": "volume", "वोल्यूम": "volume",
    "बढ़ाओ": "badhao", "तेज": "tez", "कम": "kam", "धीमी": "dheemi", "म्यूट": "mute",
    "मौसम": "weather", "न्यूज़": "news", "खबर": "news", "समाचार": "news",
    "समय": "samay", "टाइम": "time", "वक्त": "waqt", "तारीख": "tareekh", "डेट": "date",

    # Connectors, Fillers & Common Words
    "ए": "a", "को": "ko", "पर": "par", "पे": "pe", "में": "me", "और": "aur",
    "करो": "karo", "कर": "kar", "कीजिए": "kijiye", "दो": "do", "दीजिए": "dijiye",
    "प्लीज": "please", "कृपया": "kripya", "जरा": "zara", "जल्दी": "jaldi", "यार": "yaar", "भाई": "bhai",
    "का": "ka", "की": "ki", "के": "ke", "से": "se",
    "गाना": "gaana", "गाने": "gaane", "गीत": "geet", "म्यूजिक": "music", "संगीत": "music", "वीडियो": "video",
    "नमस्ते": "namaste", "हेलो": "hello", "हाय": "hi", "सुनो": "suno", "अलविदा": "alvida", "बाय": "bye",
    "धन्यवाद": "dhanyawad", "शुक्रिया": "shukriya"
}


def transliterate_indic_command(text: str) -> str:
    """
    Translates/transliterates Indic (Devanagari) script keywords to Latin Hinglish
    for seamless deterministic routing and fallback processing.
    Preserves non-dictionary tokens (like unique contact names or search payloads).
    """
    if not text:
        return ""
    res = text
    for phrase, repl in DEV_PHRASES:
        res = res.replace(phrase, repl)
    tokens = res.split()
    out = []
    for t in tokens:
        clean_t = t.strip(".,!?;:\"'()[]{}")
        repl = DEV_TO_LATIN.get(clean_t, clean_t)
        out.append(repl)
    return " ".join(out)


def normalize_command(text: str) -> str:
    """Normalizes raw input text by standardizing spaces, punctuation, case, and transliterating Indic/Devanagari keywords."""
    if not text:
        return ""
    norm = text.strip()
    norm = transliterate_indic_command(norm)
    norm = norm.lower()
    # Normalize multiple whitespace
    norm = re.sub(r'\s+', ' ', norm)
    return norm


def clean_compound_clauses(text: str) -> str:
    """
    Splits compound commands joined by conjunctions like:
    'WhatsApp open karo aur Rahul ko hello bhejo'
    'WhatsApp kholo and Rahul ko message karo'
    Returns the core command clause for parameter extraction.
    """
    norm = normalize_command(text)

    # Detect WhatsApp opening prefix followed by conjunction
    # e.g., 'whatsapp open karo aur/and/then/phir/fir ...'
    prefix_match = re.match(
        r'^(?:whatsapp|whats app|wa)\s*(?:ko)?\s*(?:open|kholo|chalao|start|launch)(?:\s+karo|\s+do)?\s+(?:aur|and|then|phir|fir|,|\&)\s+(.*)',
        norm,
        re.IGNORECASE
    )
    if prefix_match:
        return prefix_match.group(1).strip()

    # Detect English style: 'open whatsapp and/then ...'
    en_prefix = re.match(
        r'^(?:open|launch|start)\s+(?:whatsapp|whats app|wa)(?:\s+web)?\s+(?:and|then|,|\&)\s+(.*)',
        norm,
        re.IGNORECASE
    )
    if en_prefix:
        return en_prefix.group(1).strip()

    return norm


def clean_contact_name(c: Optional[str]) -> Optional[str]:
    """Cleans up candidate contact name, strips syntax noise and verifies against blacklist."""
    if not c:
        return None
    c = c.strip().strip("\"'")
    c = re.sub(r'^(?:to|par|pe|me|mein|on|via)\s+', '', c, flags=re.I).strip()
    c = re.sub(r'\s+(?:ko|par|pe|me|mein|on\s+whatsapp|via\s+whatsapp)$', '', c, flags=re.I).strip()
    c = re.sub(r'\s+', ' ', c).strip()
    for filler in ["please", "zara", "jaldi", "yaar", "bhai", "karo", "bhejo", "do", "likho", "send"]:
        c = re.sub(rf'\b{filler}\b', '', c, flags=re.IGNORECASE).strip()
    c = re.sub(r'\s+', ' ', c).strip()
    if not c or c.lower() in CONJUNCTIONS or c.lower() in NON_CONTACT_WORDS:
        return None
    return c


def extract_whatsapp_parameters(raw_text: str) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Extracts (contact_name, message, is_missing_message) from a WhatsApp utterance.
    Supports Hindi, Hinglish, and English phrasing.
    Supports multi-word contacts (e.g. 'Aarti School', 'Rahul Sharma'),
    Hindi/Hinglish conjunction handling ('aur bolo kal milte hain'),
    and phone numbers.
    Strictly prevents conjunctions ('and', 'aur', 'then', 'phir', 'fir') from being
    extracted as the contact name.
    """
    norm = normalize_command(raw_text)
    clause = clean_compound_clauses(norm)

    # 0. Phone number check (10+ digits)
    digits = ''.join(filter(str.isdigit, clause))
    if len(digits) >= 10:
        m_phone_hi = re.search(r'(\+?\d[\d\s-]{8,}\d)\s+ko\s+(.*)', clause, re.I)
        m_phone_en = re.search(r'(?:to\s+)?(\+?\d[\d\s-]{8,}\d)\s*(.*)', clause, re.I)
        if m_phone_hi:
            p = ''.join(filter(str.isdigit, m_phone_hi.group(1)))
            rest = m_phone_hi.group(2).strip()
            rest = re.sub(r'\b(?:message|msg)?\s*(?:bhejo|send\s*karo|karo|bhej\s*do|send)\b', '', rest, flags=re.I).strip()
            return p, rest or None, (not bool(rest))
        elif m_phone_en:
            p = ''.join(filter(str.isdigit, m_phone_en.group(1)))
            rest = m_phone_en.group(2).strip()
            rest = re.sub(r'^(?:saying|say|with\s+message|that|message|msg)\s*', '', rest, flags=re.I).strip()
            return p, rest or None, (not bool(rest))

    # 1. Hindi / Hinglish with 'ko':
    # e.g., 'WhatsApp par Aarti School ko message bhejo hello'
    # e.g., 'WhatsApp par Aarti School ko message bhejo aur bolo kal milte hain'
    # e.g., 'Aarti School ko WhatsApp pe hello send karo'
    # e.g., 'WhatsApp me Aarti School ko message karo hello'
    # e.g., 'Aarti School ko message bhejo hello'
    # e.g., 'Kasyap ko hi bhejo'
    # e.g., 'WhatsApp me Rahul ko message karo'
    m_hi_ko = re.search(
        r'(?:whatsapp\s*(?:me|mein|par|pe)?\s*)?([a-zA-Z0-9_\u0900-\u097F\s]+?)\s+ko\s+(.*)',
        clause,
        re.I
    )
    if m_hi_ko:
        c_raw = m_hi_ko.group(1).strip()
        c_raw = re.sub(r'^(?:whatsapp\s*(?:me|mein|par|pe)?\s*)', '', c_raw, flags=re.I).strip()
        c = clean_contact_name(c_raw)
        rest = m_hi_ko.group(2).strip()
        rest_clean = re.sub(r'\b(?:whatsapp\s*(?:par|me|mein|pe)?)\b', '', rest, flags=re.I).strip()

        # Conjunction handling in message: 'message bhejo aur bolo kal milte hain'
        m_conj = re.search(r'\b(?:message|msg|chat)?\s*(?:bhejo|send\s*karo|bhej\s*do|karo|do)?\s*(?:aur|and|then|phir|fir)\s+(?:bolo|keh\s*do|bolna|say|kehna|send\s*karo|bhejo)?\s*(.*)', rest_clean, re.I)
        if m_conj and m_conj.group(1).strip():
            return c, m_conj.group(1).strip(), False

        # Message payload after verb: 'message bhejo hello', 'bolo hello', 'message karo hello'
        m_verb_after = re.search(r'^(?:message|msg|chat|text)?\s*(?:bhejo|send\s*karo|bhej\s*do|karo|do|bolo|likho)\s+(.+)$', rest_clean, re.I)
        if m_verb_after and m_verb_after.group(1).strip():
            return c, m_verb_after.group(1).strip(), False

        # Message payload before verb: 'hello send karo', 'kal milte hain bhejo', 'hi bhejo', 'kya kar rahe ho message bhejo'
        m_verb_before = re.search(r'^(.+?)\s*(?:message|msg)?\s+(?:bhejo|send\s*karo|bhej\s*do|send\s*kar\s*do|karo|bolo|likho|send)$', rest_clean, re.I)
        if m_verb_before and m_verb_before.group(1).strip():
            cand_m = m_verb_before.group(1).strip()
            if cand_m.lower() not in ['message', 'msg', 'chat', 'text', 'par message']:
                return c, cand_m, False
            return c, None, True

        # If rest is empty or just 'message karo', message is missing
        if not rest_clean or rest_clean.lower() in ['message', 'msg', 'chat', 'message karo', 'message bhejo', 'send karo', 'bhejo']:
            return c, None, True

        return c, rest_clean, False

    # 2. English with saying / say / with message / that / :
    # e.g., 'reply to Rohit on whatsapp saying I will reach in 10 minutes'
    # e.g., 'Send a WhatsApp message to Aarti School saying hello'
    # e.g., 'reply to Rohit saying I will be late'
    # e.g., 'send a message to rahul on whatsapp saying hello'
    m_en_say = re.search(
        r'^(?:send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|chat|text)|reply(?:\s+to)?|text|message|whatsapp)\s+(?:to\s+)?([a-zA-Z0-9_\u0900-\u097F\s]+?)(?:\s+(?:on|via|in)\s+whatsapp)?\s+(?:saying|say|with\s+message|that|ki|ke|\:)\s*[:\s]*(.*)',
        clause,
        re.I
    )
    if m_en_say:
        c = clean_contact_name(m_en_say.group(1))
        m = m_en_say.group(2).strip()
        if c:
            return c, m if m else None, (not bool(m))

    # 3. English with 'on whatsapp':
    # e.g., 'Text Aarti School on WhatsApp hello'
    # e.g., 'Send message to Aarti School on WhatsApp hello'
    m_en_on_wa = re.search(
        r'^(?:send\s+(?:a\s+)?(?:message|msg|chat|text)\s+(?:to\s+)?|text\s+|message\s+|reply\s+(?:to\s+)?)([a-zA-Z0-9_\u0900-\u097F\s]+?)\s+(?:on|via|in)\s+whatsapp\s*(.*)',
        clause,
        re.I
    )
    if m_en_on_wa:
        c = clean_contact_name(m_en_on_wa.group(1))
        m = m_en_on_wa.group(2).strip()
        if c:
            return c, m if m else None, (not bool(m))

    # 4. English greeting payload:
    # e.g., 'Send WhatsApp message to Aarti School hello'
    m_en_greet = re.search(
        r'^(?:send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|chat|text)|whatsapp)\s+(?:to\s+)?([a-zA-Z0-9_\u0900-\u097F\s]+?)\s+(hello|hi|hey|good\s+morning|good\s+evening|namaste|kem\s+cho)\b(.*)',
        clause,
        re.I
    )
    if m_en_greet:
        c = clean_contact_name(m_en_greet.group(1))
        m = (m_en_greet.group(2) + m_en_greet.group(3)).strip()
        if c:
            return c, m, False

    # 5. English direct / chat:
    # e.g., 'send chat shivam say hello'
    # e.g., 'chat rahul hello'
    m_chat = re.search(
        r'^(?:send\s+chat|chat)\s+([a-zA-Z0-9_\u0900-\u097F]+)\s+(?:say\s+|saying\s+)?(.*)',
        clause,
        re.I
    )
    if m_chat:
        c = clean_contact_name(m_chat.group(1))
        m = m_chat.group(2).strip()
        if c:
            return c, m if m else None, (not bool(m))

    # 6. English missing message:
    # e.g., 'Send a message to Pranshul', 'Send a WhatsApp message to Aarti School'
    m_en_miss = re.search(
        r'^(?:send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|msg|chat|text)|text|message|reply(?:\s+to)?)\s+(?:to\s+)([a-zA-Z0-9_\u0900-\u097F\s]+?)$',
        clause,
        re.I
    )
    if m_en_miss:
        c = clean_contact_name(m_en_miss.group(1))
        if c:
            return c, None, True

    return None, None, False


class CommandRouter:
    """
    Evaluates raw command input, determines intent with strict priority ordering,
    and returns routing metadata.
    """

    @staticmethod
    def route_command(raw_text: str) -> Dict[str, Any]:
        raw_cmd = raw_text.strip() if raw_text else ""
        norm_cmd = normalize_command(raw_cmd)

        logger.info(f"[CommandRouter] Original command: \"{raw_cmd}\"")
        logger.info(f"[Router] Raw command: \"{raw_cmd}\"")
        logger.info(f"[Router] Normalized command: \"{norm_cmd}\"")

        candidates: List[str] = []

        # Check for WhatsApp indicators
        has_insta_kw = bool(re.search(r'\b(?:instagram|insta|ig)\b', norm_cmd))
        has_whatsapp_kw = bool(re.search(r'\b(?:whatsapp|whats\s*app|wa)\b', norm_cmd))
        has_messaging_syntax = (
            not has_insta_kw
            and (
                bool(re.search(r'\b[a-zA-Z0-9_\u0900-\u097F]+\s+ko\s+.*\b(?:bhejo|send|message|msg|bolo|reply)\b', norm_cmd))
                or bool(re.search(r'\b(?:send\s+(?:a\s+)?(?:message|msg|chat)|reply|chat|text|message)\s+(?:to\s+)?[a-zA-Z0-9_\u0900-\u097F]+', norm_cmd))
                or any(k in norm_cmd for k in ["unread message", "kiska message", "send message", "send a message", "send msg", "send chat", "chat with", "reply to"])
            )
        )
        is_whatsapp_candidate = (has_whatsapp_kw or has_messaging_syntax) and not has_insta_kw
        if is_whatsapp_candidate:
            candidates.append("whatsapp")

        # Check for explicit Windows Settings indicators
        is_settings_candidate = bool(re.search(r'\b(?:settings|setting)\b', norm_cmd))
        if is_settings_candidate:
            candidates.append("settings")

        # Check for app opening
        is_open_candidate = any(kw in norm_cmd for kw in ["open", "kholo", "start", "launch", "chalao"])
        if is_open_candidate:
            candidates.append("open_app")

        # Check for web search
        is_search_candidate = any(kw in norm_cmd for kw in ["search", "dhoondo", "find"])
        if is_search_candidate:
            candidates.append("web_search")

        logger.info(f"[Router] Intent candidates: {candidates}")

        # -----------------------------------------------------------------
        # PRIORITY 1: WHATSAPP INTENT (HIGHEST PRIORITY - TERMINAL ROUTE)
        # -----------------------------------------------------------------
        # If the utterance contains WhatsApp indicators, it must NEVER fall through
        # to Windows Search or Settings.
        if is_whatsapp_candidate and not is_settings_candidate:
            logger.info("[Router] WhatsApp detected: true")
            logger.info("[Router] Windows Search allowed: false")
            logger.info(f"[WhatsApp Router] Raw command: \"{raw_cmd}\"")

            # Check if this is bare unread messages request
            if any(k in norm_cmd for k in ["unread", "kiska", "check", "dekho", "aaya", "padho", "read", "naye"]) and any(k in norm_cmd for k in ["message", "messages", "whatsapp", "chat"]):
                logger.info("[CommandRouter] Detected intent: get_whatsapp_unread")
                logger.info("[Router] Selected intent: get_whatsapp_unread")
                logger.info("[WhatsApp Router] Intent: get_whatsapp_unread")
                return {
                    "intent": "get_whatsapp_unread",
                    "raw_command": raw_cmd,
                    "normalized_command": norm_cmd,
                    "is_terminal": True
                }

            # Check if bare WhatsApp open command (without sending message)
            is_bare_open = (
                has_whatsapp_kw
                and any(kw in norm_cmd for kw in ["open", "kholo", "chalao", "start", "launch", "web"])
                and not any(kw in norm_cmd for kw in ["send", "bhej", "bhejo", "bolo", "say", "saying", "that", "msg", "message", "chat", "reply", "jawab"])
                and not has_messaging_syntax
            )
            if is_bare_open:
                logger.info("[CommandRouter] Detected intent: open_whatsapp")
                logger.info("[Router] Selected intent: open_whatsapp")
                logger.info("[WhatsApp Router] Intent: open_whatsapp")
                return {
                    "intent": "open_whatsapp",
                    "raw_command": raw_cmd,
                    "normalized_command": norm_cmd,
                    "is_terminal": True
                }

            # Extract contact and message
            contact, message, missing_msg = extract_whatsapp_parameters(raw_cmd)

            if contact:
                logger.info(f"[WhatsApp] Contact: \"{contact}\"")
                logger.info(f"[WhatsApp] Message: \"{message or ''}\"")
                logger.info(f"[WhatsApp Router] Contact: \"{contact}\"")
                logger.info(f"[WhatsApp Router] Message: \"{message or ''}\"")

                if missing_msg or not message:
                    logger.info("[CommandRouter] Detected intent: whatsapp_clarification_needed")
                    logger.info("[Router] Selected intent: whatsapp_clarification_needed")
                    logger.info("[WhatsApp Router] Intent: whatsapp_clarification_needed")
                    return {
                        "intent": "whatsapp_clarification_needed",
                        "contact": contact.capitalize(),
                        "raw_command": raw_cmd,
                        "normalized_command": norm_cmd,
                        "is_terminal": True
                    }

                logger.info("[CommandRouter] Detected intent: whatsapp_message")
                logger.info("[Router] Selected intent: whatsapp_message")
                logger.info("[WhatsApp Router] Intent: whatsapp_message")
                return {
                    "intent": "whatsapp_message",
                    "contact": contact,
                    "message": message,
                    "raw_command": raw_cmd,
                    "normalized_command": norm_cmd,
                    "is_terminal": True
                }
            elif has_whatsapp_kw:
                # WhatsApp utterance without recognizable contact
                logger.info("[CommandRouter] Detected intent: whatsapp_contact_clarification_needed")
                logger.info("[Router] Selected intent: whatsapp_contact_clarification_needed")
                logger.info("[WhatsApp Router] Intent: whatsapp_contact_clarification_needed")
                return {
                    "intent": "whatsapp_contact_clarification_needed",
                    "raw_command": raw_cmd,
                    "normalized_command": norm_cmd,
                    "is_terminal": True
                }

        # -----------------------------------------------------------------
        # PRIORITY 2: EXPLICIT WINDOWS SETTINGS INTENT
        # -----------------------------------------------------------------
        # Windows Settings should execute ONLY when the user explicitly asks
        # for settings (e.g., 'WiFi settings kholo', 'Bluetooth settings kholo',
        # 'Settings open karo', 'Windows settings kholo').
        if is_settings_candidate:
            logger.info("[Router] WhatsApp detected: false")
            logger.info("[Router] Windows Search allowed: false")

            target_setting = "settings"
            if any(k in norm_cmd for k in ["wifi", "wi-fi", "internet", "wireless"]):
                target_setting = "wifi settings"
            elif any(k in norm_cmd for k in ["bluetooth", "bt"]):
                target_setting = "bluetooth settings"
            elif any(k in norm_cmd for k in ["display", "screen", "resolution"]):
                target_setting = "display settings"
            elif any(k in norm_cmd for k in ["sound", "audio", "speaker", "mic"]):
                target_setting = "sound settings"
            elif any(k in norm_cmd for k in ["network"]):
                target_setting = "network settings"

            logger.info(f"[Settings] Target: {target_setting}")
            logger.info("[CommandRouter] Detected intent: open_settings")
            logger.info("[Router] Selected intent: open_settings")
            return {
                "intent": "open_settings",
                "setting_target": target_setting,
                "raw_command": raw_cmd,
                "normalized_command": norm_cmd,
                "is_terminal": True
            }

        # -----------------------------------------------------------------
        # PRIORITY 3: EXPLICIT APPLICATION LAUNCH / CLOSE
        # -----------------------------------------------------------------
        if is_open_candidate and not has_whatsapp_kw:
            logger.info("[Router] WhatsApp detected: false")
            logger.info("[Router] Windows Search allowed: false")
            logger.info("[CommandRouter] Detected intent: open_app")
            logger.info("[Router] Selected intent: open_app")
            return {
                "intent": "open_app",
                "raw_command": raw_cmd,
                "normalized_command": norm_cmd,
                "is_terminal": False
            }

        # -----------------------------------------------------------------
        # PRIORITY 4: EXPLICIT WEB SEARCH
        # -----------------------------------------------------------------
        if is_search_candidate and not has_whatsapp_kw:
            logger.info("[Router] WhatsApp detected: false")
            logger.info("[Router] Windows Search allowed: false")
            q = re.sub(r'^(?:search|dhoondo|find)\s+(?:for\s+)?', '', norm_cmd, flags=re.I).strip()
            q = re.sub(r'\b(?:search\s*karo|dhoondo|on\s+google|google\s+pe)\b', '', q, flags=re.I).strip()
            logger.info(f"[WindowsSearch] Query: \"{q}\"")
            logger.info("[CommandRouter] Detected intent: web_search")
            logger.info("[Router] Selected intent: web_search")
            return {
                "intent": "web_search",
                "query": q,
                "raw_command": raw_cmd,
                "normalized_command": norm_cmd,
                "is_terminal": True
            }

        # -----------------------------------------------------------------
        # PRIORITY 5: GENERAL / FALLBACK CONVERSATION
        # -----------------------------------------------------------------
        logger.info("[Router] WhatsApp detected: false")
        logger.info("[Router] Windows Search allowed: false")
        logger.info("[CommandRouter] Detected intent: general")
        logger.info("[Router] Selected intent: general")
        return {
            "intent": "general",
            "raw_command": raw_cmd,
            "normalized_command": norm_cmd,
            "is_terminal": False
        }
