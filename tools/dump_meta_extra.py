"""HTTP-сервисы, общие реквизиты, определяемые типы, состав плана обмена."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.meta_dump import dump_folder_path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _synonym(text: str) -> str:
    m = re.search(
        r"<v8:lang>ru</v8:lang>\s*<v8:content>([^<]+)</v8:content>",
        text,
    )
    return m.group(1).strip() if m else ""


def _tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return (m.group(1) or "").strip() if m else ""


def http_service_map(
    request: str = "",
    *,
    name: str = "",
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = dump_folder_path("HTTPServices", code_dir)
    if not root:
        return {"ok": False, "error": "Нет HTTPServices/ в выгрузке", "services": []}
    want = (name or "").strip()
    q = (request or "").lower().replace("ё", "е")
    from tools.phrase_struct import name_filter_tokens

    toks = [t for t in name_filter_tokens(request) if not re.fullmatch(r"http\w*", t)]
    services: list[dict[str, Any]] = []
    for xml in sorted(root.glob("*.xml"), key=lambda p: p.name.lower()):
        text = _read(xml)
        if "<HTTPService" not in text:
            continue
        nm = _tag(text, "Name") or xml.stem
        blob = f"{nm} {_synonym(text)} {_tag(text, 'RootURL')}".lower().replace("ё", "е")
        if want and want.lower() not in blob:
            continue
        if not want and toks and not any(t in blob for t in toks):
            continue
        templates: list[dict[str, Any]] = []
        for tm in re.finditer(
            r"<URLTemplate\b[^>]*>\s*<Properties>(.*?)</Properties>(.*?)</URLTemplate>",
            text,
            re.S | re.I,
        ):
            body, rest = tm.group(1), tm.group(2)
            tname = _tag(body, "Name")
            path = _tag(body, "Template")
            methods = []
            for mm in re.finditer(
                r"<Method\b[^>]*>\s*<Properties>(.*?)</Properties>",
                rest,
                re.S | re.I,
            ):
                mb = mm.group(1)
                methods.append(
                    {
                        "name": _tag(mb, "Name"),
                        "http_method": _tag(mb, "HTTPMethod"),
                        "handler": _tag(mb, "Handler"),
                    }
                )
            templates.append({"name": tname, "template": path, "methods": methods})
        services.append(
            {
                "name": nm,
                "full_name": f"HTTPСервис.{nm}",
                "synonym": _synonym(text),
                "root_url": _tag(text, "RootURL"),
                "templates": templates,
                "path": str(xml).replace("\\", "/"),
            }
        )
        if want:
            break
    return {
        "ok": True,
        "count": len(services),
        "services": services,
        "source": "config_dump HTTPServices/*.xml",
        "prefer": (
            "root_url + templates[].template + methods (GET/POST → handler). "
            "Код — HTTPServices/<name>/Ext/Module.bsl."
        ),
    }


def common_attr_and_types(
    request: str = "",
    *,
    name: str = "",
    object_name: str = "",
    kind: str = "",
    limit: int = 40,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    kind=attr|type|'' (оба). Состав общих реквизитов / типы определяемого типа.
    Имена только из этой выгрузки.
    """
    pl = (request or "").lower().replace("ё", "е")
    want_attr = kind in ("attr", "attribute", "реквизит") or (
        not kind and re.search(r"общ\w*\s+реквизит|commonattribute", pl)
    )
    want_type = kind in ("type", "тип", "defined") or (
        not kind and re.search(r"определяем\w*\s+тип|definedtype", pl)
    )
    if not want_attr and not want_type:
        want_attr = want_type = True

    hint = (name or object_name or "").strip()
    limit = max(1, min(int(limit or 40), 80))
    out: dict[str, Any] = {"ok": True, "source": "config_dump"}

    if want_attr:
        root = dump_folder_path("CommonAttributes", code_dir)
        items: list[dict[str, Any]] = []
        if root:
            for xml in sorted(root.glob("*.xml"), key=lambda p: p.name.lower()):
                text = _read(xml)
                nm = _tag(text, "Name") or xml.stem
                members: list[dict[str, str]] = []
                for im in re.finditer(
                    r"<xr:Metadata>([^<]+)</xr:Metadata>\s*<xr:Use>([^<]+)</xr:Use>",
                    text,
                ):
                    meta, use = im.group(1).strip(), im.group(2).strip()
                    if hint and hint.lower() not in meta.lower() and hint.lower() not in nm.lower():
                        continue
                    members.append({"metadata": meta, "use": use})
                if hint and hint.lower() not in nm.lower().replace("ё", "е") and not members:
                    continue
                used = [m for m in members if m["use"].lower() != "dontuse"]
                items.append(
                    {
                        "name": nm,
                        "full_name": f"ОбщийРеквизит.{nm}",
                        "synonym": _synonym(text),
                        "content_used": used[:limit],
                        "content_used_count": len(used),
                        "content_total": len(members),
                    }
                )
                if len(items) >= limit:
                    break
        out["common_attributes"] = items
        out["common_attributes_count"] = len(items)

    if want_type:
        root = dump_folder_path("DefinedTypes", code_dir)
        items = []
        if root:
            for xml in sorted(root.glob("*.xml"), key=lambda p: p.name.lower()):
                text = _read(xml)
                nm = _tag(text, "Name") or xml.stem
                if hint and hint.lower().replace("ё", "е") not in (
                    f"{nm} {_synonym(text)}".lower().replace("ё", "е")
                ):
                    continue
                types = [
                    t.replace("cfg:", "")
                    for t in re.findall(r"<v8:Type>([^<]+)</v8:Type>", text)
                ]
                items.append(
                    {
                        "name": nm,
                        "full_name": f"ОпределяемыйТип.{nm}",
                        "synonym": _synonym(text),
                        "types": types[:40],
                    }
                )
                if len(items) >= limit:
                    break
        out["defined_types"] = items
        out["defined_types_count"] = len(items)

    out["prefer"] = (
        "content_used — объекты с Use≠DontUse. types — состав определяемого типа. "
        "Не выдумывай имена вне списка."
    )
    return out


def exchange_composition(
    request: str = "",
    *,
    name: str = "",
    limit: int = 80,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = dump_folder_path("ExchangePlans", code_dir)
    if not root:
        return {"ok": False, "error": "Нет ExchangePlans/ в выгрузке", "items": []}
    want = (name or "").strip()
    q = (request or "").lower().replace("ё", "е")
    from tools.phrase_struct import name_filter_tokens

    toks = name_filter_tokens(request)
    plans: list[dict[str, Any]] = []
    for xml in sorted(root.glob("*.xml"), key=lambda p: p.name.lower()):
        text = _read(xml)
        if "<ExchangePlan" not in text:
            continue
        nm = _tag(text, "Name") or xml.stem
        blob = f"{nm} {_synonym(text)}".lower().replace("ё", "е")
        if want and want.lower() not in blob:
            continue
        if not want and toks and not any(t in blob for t in toks):
            # still allow if later content search
            pass
        content_path = root / nm / "Ext" / "Content.xml"
        ctext = _read(content_path)
        items: list[dict[str, str]] = []
        for im in re.finditer(
            r"<Metadata>([^<]+)</Metadata>\s*<AutoRecord>([^<]+)</AutoRecord>",
            ctext,
        ):
            meta, ar = im.group(1).strip(), im.group(2).strip()
            if toks and not any(t in meta.lower() for t in toks) and toks and not any(
                t in blob for t in toks
            ):
                continue
            items.append({"metadata": meta, "auto_record": ar})
            if len(items) >= limit:
                break
        if not want and toks and not any(t in blob for t in toks) and not items:
            continue
        plans.append(
            {
                "name": nm,
                "full_name": f"ПланОбмена.{nm}",
                "synonym": _synonym(text),
                "content_count": len(items),
                "content": items[:limit],
                "content_path": str(content_path).replace("\\", "/")
                if content_path.is_file()
                else "",
                "truncated": len(items) >= limit,
            }
        )
        if want or len(plans) >= 8:
            if want:
                break
    return {
        "ok": True,
        "count": len(plans),
        "plans": plans,
        "source": "ExchangePlans/*.xml + Ext/Content.xml",
        "prefer": (
            "metadata + auto_record (Allow/Deny). Это состав плана, не runtime-регистрации узлов."
        ),
    }
