"""
title: 1C external files (.epf/.erf/.cfe)
description: Забирает вложения 1С из чата, отправляет в rag_agent MCP (uploads/) и убирает из запроса к модели.
version: 0.2

Установка: Open WebUI -> Admin -> Functions -> + -> вставить этот файл целиком,
включить, затем включить Global (или привязать к модели 1c_agent).
Valves: upload_url, token (= RAG_UPLOAD_TOKEN из .env проекта).
"""

import asyncio
import inspect
import re
from urllib.parse import quote

import requests
from pydantic import BaseModel, Field

_NAME = re.compile(r"\.(epf|erf|cfe)(\.[A-Za-z0-9]{1,5})?$", re.I)


class Filter:
    class Valves(BaseModel):
        upload_url: str = Field(
            default="http://host.docker.internal:8100/upload",
            description="Адрес /upload MCP-сервера rag_agent",
        )
        token: str = Field(default="", description="RAG_UPLOAD_TOKEN из .env проекта")

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    async def _maybe_await(v):
        return await v if inspect.isawaitable(v) else v

    async def _read_bytes(self, file_id: str):
        from open_webui.models.files import Files
        from open_webui.storage.provider import Storage

        rec = await self._maybe_await(Files.get_file_by_id(file_id))
        if not rec:
            return None, ""
        local = Storage.get_file(rec.path)
        with open(local, "rb") as fh:
            return fh.read(), (rec.filename or (rec.meta or {}).get("name") or "")

    def _upload(self, name: str, data: bytes) -> str:
        """Путь файла на сервере rag_agent ("" — не удалось)."""
        r = requests.post(
            self.valves.upload_url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.valves.token}",
                "X-Filename": quote(name),
                "Content-Type": "application/octet-stream",
            },
            timeout=60,
        )
        if r.status_code != 200:
            return ""
        return str((r.json() or {}).get("path") or name)

    async def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        files = body.get("files") or []
        keep, taken = [], []
        for f in files:
            info = f.get("file") or f
            name = info.get("filename") or (info.get("meta") or {}).get("name") or f.get("name") or ""
            fid = info.get("id") or f.get("id")
            if not (name and fid and _NAME.search(name)):
                keep.append(f)
                continue
            try:
                data, real = await self._read_bytes(fid)
                m = _NAME.search(name)
                clean = name[: m.start()] + "." + m.group(1).lower()
                path = await asyncio.to_thread(self._upload, clean, data) if data else ""
                if path:
                    taken.append(path)
                    continue
            except Exception:
                pass
            keep.append(f)  # не получилось — оставить как есть
        if not taken:
            return body
        body["files"] = keep
        note = "\n\n[Приложены файлы 1С (уже загружены): " + "; ".join(taken) + (
            ". Вызови inspect_external_processing(path=<этот путь целиком>, request=вопрос пользователя).]"
        )
        msgs = body.get("messages") or []
        for m in reversed(msgs):
            if m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                m["content"] = c + note
            elif isinstance(c, list):
                m["content"] = [
                    p for p in c if not (isinstance(p, dict) and p.get("type") == "image_url")
                ] + [{"type": "text", "text": note.strip()}]
            break
        return body
