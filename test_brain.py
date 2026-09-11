import sys
import asyncio
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from core.brain import process_command

async def test_brain_10_tools():
    test_queries = [
        "Abhi time aur ISO timestamp kya hai?",
        "Clipboard check karo",
        "Spotify par lo-fi beats chalao",
        "Dev environment start karo",
        "Odoo server status check karo",
        "Odoo server par account module update karo",
        "Notepad kholo",
        "Volume badhao"
    ]

    print("Running Brain 10-Tool intent processing tests...")
    for q in test_queries:
        res = await process_command(q)
        print(f"\n[Prompt]: {q}")
        print(f"[Reply] : {res.get('reply')}")
        print(f"[Action]: {res.get('action')}")
        assert res.get("reply"), f"No reply received for query: {q}"

    print("\nALL 10-TOOL BRAIN INTENT TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(test_brain_10_tools())
