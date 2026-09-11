# 🎙️ Astra - AI Voice Automated Assistant (Gemini Live)

An intelligent, voice-controlled desktop assistant powered by **Google Gemini AI with native Tool Calling**, featuring ultra-realistic neural speech synthesis and seamless Windows PC automation.

---

## ✨ Features

- 🧠 **Gemini AI Intelligence**: Understands natural voice commands in **Hindi, English, and Hinglish** ("Bhai YouTube pe Arijit Singh ke gaane chala de", "Notepad kholo", "Volume thoda badhana", "What is today's date?").
- ⚡ **Zero PyAudio Hassle**: Uses the browser's high-accuracy **Web Speech API** for instant, low-latency multilingual speech recognition.
- 🗣️ **Ultra-Realistic Neural TTS**: High-definition bilingual voice auto-switching powered by Microsoft Edge Neural voices (`hi-IN-SwaraNeural` and `en-IN-NeerjaNeural`).
- 🔮 **Futuristic UI**: Gemini Live style glowing neural orb visualizer with animated rings reflecting states (Listening 🟢, Thinking 🟣, Speaking 🔵).
- 🖥️ **Windows PC Automation**:
  - **App Opener/Closer**: Opens and closes Chrome, VS Code, Notepad, Calculator, Paint, Task Manager, File Explorer, etc.
  - **Web & YouTube**: Searches YouTube videos, opens websites, searches Google.
  - **WhatsApp**: Pre-fills messages and opens WhatsApp Web reliably.
  - **System Control**: Adjusts volume (up/down/mute), takes screenshots, locks PC.
- 📱 **Mobile / LAN Ready**: Configured on `0.0.0.0` so you can interact with your assistant from your mobile phone or tablet on the same Wi-Fi.

---

## 🚀 Quick Start

### 1. Launch with One Click
Simply double-click:
```
run.bat
```
or run in terminal:
```bash
python run.py
```
This will automatically launch the server and open the assistant interface in your default browser at `http://localhost:8000`.

---

## 🎙️ How to Use

1. **Speak a Command**:
   - Click the **Microphone button** (or press & hold the **Spacebar**).
   - Speak your command in Hindi or English.
2. **Watch the Magic**:
   - Astra understands your intent, executes the requested action on Windows, and answers back in spoken voice.

### Sample Voice Commands:

| Intent | Hindi / Hinglish Voice Command | English Voice Command |
| :--- | :--- | :--- |
| **Open App** | *"Notepad kholo"* / *"Chrome open karo"* | *"Open Visual Studio Code"* |
| **Close App** | *"Notepad band kar do"* | *"Close Chrome"* |
| **YouTube** | *"YouTube pe Bollywood lo-fi songs chalao"* | *"Play relaxing jazz on YouTube"* |
| **Google** | *"Google par latest AI news search karo"* | *"Search weather forecast on Google"* |
| **WhatsApp** | *"WhatsApp par message likho 'Hey bro' "* | *"Send WhatsApp message 'Meeting at 5'* |
| **Volume** | *"Volume badhao"* / *"Volume kam karo"* | *"Mute audio"* |
| **Screenshot**| *"Ek screenshot le lo"* | *"Take a screenshot"* |
| **Date & Time**| *"Abhi samay kya hua hai?"* | *"What's the date and time today?"* |

---

## ⚙️ Configuration & API Key

You can configure your Gemini API Key in two easy ways:
1. **From the Web UI**: Click the ⚙️ (Settings) icon on the top right, paste your API key, and click **Save Key**.
2. **From the `.env` file**: Open `.env` and set:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```
*(Get a free API key from [Google AI Studio](https://aistudio.google.com/)).*

> **Note**: Even if no API key is provided, the assistant includes a built-in **Rule-Based Fallback Engine** so basic commands (open apps, volume, websites, time) continue to work offline!

---

## 📱 Connecting from Mobile / LAN (Windows Firewall Setup)

Astra runs as a Progressive Web App (PWA) allowing you to control your laptop directly from your phone or tablet on the same Wi-Fi network.

### 1. Allow Windows Firewall Rule (Required Once)
Windows Firewall frequently blocks inbound TCP connections from other devices on your local network by default. To allow mobile connections, open **PowerShell** or **Command Prompt** as **Administrator** and run:

```cmd
netsh advfirewall firewall add rule name="Astra" dir=in action=allow protocol=TCP localport=8000
```

### 2. Verify `.env` Configuration
Ensure your `.env` contains:
```env
HOST=0.0.0.0
PORT=8000
```

### 3. Open on Mobile Device
1. Start Astra on your laptop: `python run.py`.
2. Look at the startup output for your laptop's local LAN IP:
   ```
   [*] Open on mobile : http://<LAN_IP>:8000 (LAN IP: 192.168.x.x)
   ```
3. On your phone (connected to the **same Wi-Fi**), open Chrome or Safari and browse to `http://<LAN_IP>:8000`.
   *(Do **not** use `localhost` or `127.0.0.1` on your phone — that points to the phone itself!).*
4. Tap **"Add to Home Screen"** or the install prompt to install Astra as a standalone PWA with the **"Tap to Talk"** quick-launch shortcut.

> **Mobile Wake-Word Note**: Modern mobile operating systems restrict continuous background microphone access when the screen is off or the browser is minimized. On mobile, use the **"Tap to Talk"** home screen shortcut or the persistent quick-talk notification as your primary hands-free trigger.

---

## 🔒 HTTPS via Cloudflare Tunnel (Recommended for Mobile Mic & Push)

Mobile browsers (especially iOS Safari and modern Chrome/Android) strictly require a **Secure Context (`https://`)** for:
- 🎙️ Microphone access (Web Speech API and audio recording)
- 🔔 W3C Web Push notifications and Service Worker push registration

Astra includes built-in automated support for **Cloudflare Tunnel**, providing a free, secure public HTTPS URL (`https://xxxx.trycloudflare.com`) without opening router ports or configuring SSL certificates.

### 1. Install Cloudflare Tunnel CLI (`cloudflared`)
In **PowerShell** or **Command Prompt**, run:
```cmd
winget install --id Cloudflare.cloudflared
```
*(Or download directly from [Cloudflare's website](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)).*

### 2. Enable in `.env`
Add or update in your `.env` file:
```env
USE_TUNNEL=true
```

### 3. Start Astra
Run Astra as usual:
```bash
python run.py
```
Astra will automatically start the tunnel subprocess, register CORS origins, and print your secure mobile link:
```
[*] Cloudflare Tunnel : Initializing HTTPS tunnel for mobile access...
[*] Local URL         : http://127.0.0.1:8000
[*] 📱 Open on mobile : https://xxxx.trycloudflare.com
```

### 4. Public Internet Security Model
> [!IMPORTANT]
> **Zero Security Compromise**: Exposing the assistant to the public internet via Cloudflare Tunnel strictly preserves mandatory `ASTRA_AUTH_TOKEN` authentication on all `/api/*` endpoints. Requests without the cryptographically secure token are immediately rejected with HTTP 401 Unauthorized. Never disable or weaken token validation when running with `USE_TUNNEL=true`.

---

## 📁 Project Structure

```
├── run.py                 # One-click startup script (auto browser opener)
├── run.bat                # Windows double-click shortcut
├── app.py                 # FastAPI backend server
├── config.py              # Settings & environment variable loader
├── requirements.txt       # Project dependencies
├── test_actions.py        # Automated test suite for Windows automation
├── test_tts.py            # Automated test suite for Neural TTS
├── test_brain.py          # Automated test suite for Gemini tool-calling
├── core/
│   ├── actions.py         # Windows automation (apps, websites, volume, WhatsApp)
│   ├── brain.py           # Gemini 3.6-flash tool-calling & intent engine
│   └── tts.py             # Edge Neural TTS audio generator
├── static/
│   ├── index.html         # Gemini Live Glowing Orb UI
│   ├── style.css          # Glassmorphism & Cyberpunk styling
│   ├── app.js             # Speech recognition & audio stream handler
│   └── audio/             # Generated voice response clips
└── screenshots/           # Saved PC screenshots
```
