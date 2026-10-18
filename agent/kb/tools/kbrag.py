"""RAG компании: Open WebUI и портал документации. RAG и проект человек выбирает каждый раз — значения по умолчанию нет.

Ключи и cookie — только из переменных окружения, в вывод не попадают. Ответ сохраняется в materials/ai/:
сослаться на него можно, но статус факта не выше «оценка» — ответ ИИ не факт.

Переменные: KB_OPENWEBUI_URL, KB_OPENWEBUI_KEY, KB_PORTAL_URL, KB_PORTAL_COOKIE.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import kbbase

THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
EVENT = re.compile(r"\{\s*['\"]event['\"]\s*:.*?\}\s*", re.S)
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RagError(Exception):
    pass


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RagError(f"не задана переменная окружения {name}")
    return value


def clean(text: str) -> str:
    return EVENT.sub("", THINK.sub("", text)).strip()


def post(url: str, body: dict, headers: dict, timeout: int = 120) -> bytes:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RagError(f"сервер ответил {e.code} на {url.split('?')[0]}") from None
    except urllib.error.URLError as e:
        raise RagError(f"нет связи с {url.split('/api')[0]}: {e.reason}") from None


def ask_openwebui(rag: str, question: str) -> str:
    base = env("KB_OPENWEBUI_URL").rstrip("/")          # слэш на конце ломает путь
    key = env("KB_OPENWEBUI_KEY")
    raw = post(f"{base}/api/chat/completions", {"model": rag, "messages": [{"role": "user", "content": question}]},
               {"Authorization": f"Bearer {key}"})
    try:
        data = json.loads(raw.decode("utf-8"))
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError):
        raise RagError("непонятный ответ Open WebUI") from None
    return clean(message.get("content") or "")


def ask_portal(project: str, chat: str, question: str) -> str:
    base = env("KB_PORTAL_URL").rstrip("/")
    cookie = env("KB_PORTAL_COOKIE")
    body = {"message": {"role": "user", "parts": [{"type": "text", "text": question}]},
            "metadata": {"custom": {"navigationContext": {"projectName": project}}},
            "domainIds": [], "parentId": None}
    raw = post(f"{base}/api/rag/conversations/{chat}/messages/", body, {"Cookie": cookie, "Accept": "text/event-stream"})
    parts = []
    for line in raw.decode("utf-8").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if event.get("type") == "text-delta":
            parts.append(event.get("delta") or event.get("textDelta") or "")
    return clean("".join(parts))


def save(root: Path, slug: str, question: str, where: str, answer: str) -> Path:
    folder = root / "materials" / "ai"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{kbbase.today().isoformat()}-{slug}.md"
    text = [f"# Ответ RAG: {question}", "", f"Где спросили: {where}", f"Дата: {kbbase.today().isoformat()}",
            "Ответ ИИ — не факт. Ссылаться можно, статус факта — не выше «оценка».", "", "## Ответ", ""]
    text += answer.splitlines() or ["(пусто)"]
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    return path


def run(root: Path, args: list[str]) -> int:
    opts, rest = {}, list(args)
    kind = rest.pop(0) if rest else ""
    while rest:
        key = rest.pop(0)
        if key.startswith("--") and rest:
            opts[key[2:]] = rest.pop(0)
    question = opts.get("vopros", "")
    try:
        if not question:
            raise RagError('нет вопроса: --vopros "…"')
        if opts.get("save") and not SLUG.match(opts["save"]):
            raise RagError("--save — короткое имя латиницей через дефис")
        if kind == "openwebui":
            if not opts.get("rag"):
                raise RagError("какой RAG — решает человек: --rag <модель RAG в Open WebUI>")
            answer, where = ask_openwebui(opts["rag"], question), f"Open WebUI, RAG {opts['rag']}"
        elif kind == "portal":
            if not opts.get("proekt"):
                raise RagError("какой проект — решает человек: --proekt <проект в портале>")
            chat = opts.get("chat") or os.environ.get("KB_PORTAL_CHAT", "")
            if not chat:
                raise RagError("нет чата портала: --chat <id> или переменная KB_PORTAL_CHAT")
            answer, where = ask_portal(opts["proekt"], chat, question), f"портал документации, проект {opts['proekt']}"
        else:
            raise RagError("нужно `kb rag openwebui --rag <RAG> --vopros \"…\"` или `kb rag portal --proekt <проект> --vopros \"…\"`")
    except RagError as e:
        print(f"ОШИБКА: {e}")
        return 1
    print(f"ОТВЕТ ({where}). Ответ ИИ — не факт:")
    print(answer)
    if opts.get("save"):
        path = save(root, opts["save"], question, where, answer)
        print(f"Сохранено: {kbbase.rel(root, path)} — ссылаться можно, статус не выше «оценка»")
    return 0
