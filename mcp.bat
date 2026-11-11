@echo off
rem Запуск MCP-сервера (stdio). Этот файл можно указать как command в конфиге MCP-клиента.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py
