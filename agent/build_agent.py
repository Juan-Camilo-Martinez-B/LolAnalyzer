"""
Builds the local agent with PyInstaller: dist/lolanalyzer-agent/lolanalyzer-agent.exe

Usage (from the backend folder):
    python -m pip install -r requirements-dev.txt
    python agent/build_agent.py

The desktop build copies dist/lolanalyzer-agent into the installer.
No .env, database file or test is bundled.
"""

from pathlib import Path
import sys

import PyInstaller.__main__

BACKEND = Path(__file__).resolve().parents[1]

HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "aiosqlite",
    "sqlalchemy.dialects.sqlite.aiosqlite",
]


def main() -> None:
    args = [
        str(BACKEND / "agent" / "agent_main.py"),
        "--name", "lolanalyzer-agent",
        "--onedir",
        "--noconfirm",
        "--clean",
        "--console",
        "--distpath", str(BACKEND / "dist"),
        "--workpath", str(BACKEND / "build"),
        "--specpath", str(BACKEND / "build"),
        "--paths", str(BACKEND),
        "--collect-submodules", "app",
        # The agent never talks to Gemini; keep the bundle small.
        "--exclude-module", "google.genai",
        "--exclude-module", "pytest",
    ]
    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]
    PyInstaller.__main__.run(args)
    exe = BACKEND / "dist" / "lolanalyzer-agent" / "lolanalyzer-agent.exe"
    if not exe.exists():
        sys.exit("El ejecutable del agente no se generó.")
    print(f"Agente listo: {exe}")


if __name__ == "__main__":
    main()
