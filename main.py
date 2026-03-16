import os
import sys
import asyncio
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run_bot():
    try:
        from bot.bot import main
        asyncio.run(main())
    except Exception as e:
        print(f"Bot error: {e}")

def run_web():
    from web.app import app
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("🤖 Bot thread started")
    run_web()