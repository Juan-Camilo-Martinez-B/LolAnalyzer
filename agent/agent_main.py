"""
LolAnalyzer local agent: the part of the backend that runs on the player's PC.

Electron starts it with three environment variables:
  LOLANALYZER_AGENT_TOKEN  random per-launch token the desktop must send (X-Agent-Token)
  LOLANALYZER_AGENT_PORT   port on 127.0.0.1 (default 8765)
  LOLANALYZER_CLOUD_URL    cloud API base URL for history, coach logs and AI
  LOLANALYZER_PARENT_PID   desktop process; the agent exits when it disappears

It never reads a .env file and carries no database URL or API keys.
"""

import os
import sys
import threading
import time

DEFAULT_PORT = 8765


def _configure_environment() -> tuple[int, str]:
    token = os.environ.get("LOLANALYZER_AGENT_TOKEN", "").strip()
    if len(token) < 32:
        sys.stderr.write("LolAnalyzer agent: missing or short LOLANALYZER_AGENT_TOKEN\n")
        sys.exit(2)
    try:
        port = int(os.environ.get("LOLANALYZER_AGENT_PORT", DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT

    # Settings read the environment first; secrets are blanked on purpose.
    os.environ.update({
        "APP_MODE": "local",
        "AGENT_TOKEN": token,
        "CLOUD_API_URL": os.environ.get("LOLANALYZER_CLOUD_URL", "").strip(),
        "LCU_AUTO_CONNECT": "true",
        "INIT_DB_ON_STARTUP": "false",
        "DEBUG": "false",
        "DATABASE_URL": "",
        "GROQ_API_KEY": "",
        "GEMINI_API_KEY": "",
        "RIOT_API_KEY": "",
    })
    return port, token


def _exit_with_parent() -> None:
    """The agent must not outlive the desktop app (crash or forced close)."""
    raw = os.environ.get("LOLANALYZER_PARENT_PID", "").strip()
    if not raw.isdigit():
        return
    parent = int(raw)

    def watch() -> None:
        import psutil

        while True:
            if not psutil.pid_exists(parent):
                os._exit(0)
            time.sleep(2)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def main() -> None:
    port, _token = _configure_environment()
    frozen = getattr(sys, "frozen", False)
    if not frozen:
        # Running from source: make the backend package importable.
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Settings must not pick up a developer .env from the working directory.
    os.chdir(os.path.dirname(sys.executable) if frozen else os.path.dirname(os.path.abspath(__file__)))
    _exit_with_parent()

    import uvicorn

    from app.main import create_app

    uvicorn.run(create_app("local"), host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
