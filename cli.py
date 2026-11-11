"""
CLI AI-агент для 1С с tool-calling.

Примеры:
  python cli.py
  python cli.py "Сколько справочников в конфигурации?"
  python cli.py --provider ollama --model qwen2.5:7b "Найди документы по заказам"

LLM: любой OpenAI-compatible (LM Studio, Ollama, xAI, OpenAI).
Бэкенд 1С: http (расширение) или com (файловая/серверная база).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.loop import build_agent_from_config, run_chat_cli
from backends import create_backend
from config import load_config
from tools.universal import UniversalTools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1C AI Agent (universal tools)")
    parser.add_argument("prompt", nargs="?", help="Один вопрос (без интерактива)")
    parser.add_argument("--config", default=None, help="Путь к config.yaml")
    parser.add_argument(
        "--provider",
        default=None,
        help="lmstudio|ollama|xai|openai|gemini|openrouter|aigate",
    )
    parser.add_argument("--model", default=None, help="Имя модели")
    parser.add_argument("--base-url", default=None, dest="base_url", help="LLM base URL")
    parser.add_argument("--backend", choices=["http", "com"], default=None)
    parser.add_argument("-q", "--quiet", action="store_true", help="Не печатать tool-логи")
    args = parser.parse_args(argv)

    config = load_config(args.config)

    if args.backend:
        config["backend"] = args.backend
    if args.provider:
        import os

        config["llm"]["provider"] = args.provider
        # пересобрать base_url / ключ из пресета, если не передан явно
        preset = config.get("providers", {}).get(args.provider, {})
        if not args.base_url and preset.get("base_url"):
            config["llm"]["base_url"] = preset["base_url"].rstrip("/")
        # placeholder-ключи локальных серверов — заменить на env/пресет
        cur_key = (config["llm"].get("api_key") or "").strip()
        if cur_key in ("", "lm-studio", "ollama", "not-needed"):
            env_name = preset.get("api_key_env") or ""
            if env_name and os.environ.get(env_name):
                config["llm"]["api_key"] = os.environ[env_name]
            elif preset.get("api_key"):
                config["llm"]["api_key"] = preset["api_key"]
    if args.model:
        config["llm"]["model"] = args.model
    if args.base_url:
        config["llm"]["base_url"] = args.base_url.rstrip("/")

    try:
        backend = create_backend(config)
    except Exception as e:
        print(f"Ошибка бэкенда 1С: {e}", file=sys.stderr)
        return 1

    tools = UniversalTools(
        backend,
        read_only=config["agent"]["read_only"],
        default_limit=config["agent"]["query_default_limit"],
        max_limit=config["agent"]["query_max_limit"],
        list_objects_cap=config["agent"].get("list_objects_cap", 80),
        tool_result_max_chars=config["llm"].get("tool_result_max_chars", 6000),
        compact_results=True,
        code_dir=config.get("code_dir") or "",
    )

    if args.prompt:
        agent = build_agent_from_config(config, tools)
        answer = agent.run(args.prompt, verbose=not args.quiet)
        print(answer)
        return 0

    run_chat_cli(config, tools)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
