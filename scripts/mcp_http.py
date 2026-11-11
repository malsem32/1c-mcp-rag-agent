r"""
MCP по HTTP для Open WebUI — то же, что `python main.py --transport http`.

Запуск:  .\.venv\Scripts\python.exe scripts\mcp_http.py
Open WebUI → Admin → Settings → External Tools → MCP (Streamable HTTP):
         http://host.docker.internal:8100/mcp   (+ Bearer = RAG_MCP_TOKEN, если задан)
Host/port: RAG_MCP_HOST (по умолчанию 127.0.0.1) / RAG_MCP_PORT (8100).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--transport", "http", *sys.argv[1:]]))
