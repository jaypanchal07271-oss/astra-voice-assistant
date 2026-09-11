import asyncio
from pathlib import Path
from core.tts import generate_speech_audio

async def test_tts():
    print("Testing Hindi TTS...")
    hindi_text = "नमस्ते, मैं आपकी कैसे मदद कर सकती हूँ?"
    hindi_url = await generate_speech_audio(hindi_text, force_lang="hi")
    print("Hindi audio URL:", hindi_url)
    hindi_file = Path("." + hindi_url)
    assert hindi_file.exists(), f"Hindi audio file not found at {hindi_file}"
    assert hindi_file.stat().st_size > 500, "Hindi audio file is too small or empty"
    print(f"Hindi audio generated successfully ({hindi_file.stat().st_size} bytes)")

    print("\nTesting English TTS...")
    eng_text = "Hello! How can I assist you today?"
    eng_url = await generate_speech_audio(eng_text, force_lang="en")
    print("English audio URL:", eng_url)
    eng_file = Path("." + eng_url)
    assert eng_file.exists(), f"English audio file not found at {eng_file}"
    assert eng_file.stat().st_size > 500, "English audio file is too small or empty"
    print(f"English audio generated successfully ({eng_file.stat().st_size} bytes)")

    print("\nALL TTS TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(test_tts())
