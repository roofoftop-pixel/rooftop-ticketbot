"""
Entry point for Render.com deployment.
Runs the Flask web panel in a thread and the Telegram bot in the main thread.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def run_web():
    from web.app import app
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def run_bot():
    from bot.bot import main
    main()

if __name__ == "__main__":
    # Start web panel in background thread
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    print("✅ Web panel started")

    # Run bot in main thread (blocking)
    run_bot()
