"""Набор для кода (kbx.py, CI, правила) и клиенты RAG на поддельных серверах. Имена — X и R из oshibki.md."""
from __future__ import annotations

import fnmatch
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_delivery import DeliveryHelpers  # noqa: E402
from test_steps import Base  # noqa: E402

TOOLS = Path(__file__).resolve().parent.parent


def decide(rules: dict, value: str):
    result = None
    for pattern, action in rules.items():
        if fnmatch.fnmatchcase(value, pattern):
            result = action
    return result


class CodeKit(DeliveryHelpers, Base):
    def setUp(self):
        super().setUp()
        shutil.copytree(TOOLS, self.root / "tools", ignore=shutil.ignore_patterns("tests", "__pycache__"))
        project = self.root / "project.md"
        project.write_text("---\nветка базы: context\nветка кода: develop\nзакрыто агенту: [p7s-config/values/production.yaml]\n---\n",
                           encoding="utf-8")
        self.code = self.tmp.parent / (self.tmp.name + "-code")
        self.code.mkdir()

    def tearDown(self):
        shutil.rmtree(self.code, ignore_errors=True)
        super().tearDown()

    def kbx(self, *args):
        env = {k: v for k, v in os.environ.items() if k != "KB_ROOT"}
        r = subprocess.run([sys.executable, str(self.code / "kbx.py"), *args], capture_output=True, text=True,
                           encoding="utf-8", cwd=self.code, env=env)
        return r.returncode, r.stdout + r.stderr

    def test_X01_gate_refuses_without_spec(self):
        code, out = self.run_kb("code-kit", str(self.code))
        self.assertEqual(code, 0, out)
        self.assertEqual((self.code / ".kb-path").read_text().strip(), os.path.relpath(self.tmp, self.code))
        self.to_slicing()
        ident = self.new_dec()
        code, out = self.kbx("gate", ident)
        self.assertEqual(code, 1, out)
        self.assertIn("НЕЛЬЗЯ: нет кода без принятой спеки", out)
        self.run_kb("spec", "set", ident, "openspec/otbor.md")
        self.as_person("Разработчик-2")
        self.run_kb("spec", "accept", ident)
        code, out = self.kbx("gate", ident)
        self.assertEqual(code, 0, out)
        self.assertIn("МОЖНО: спека PM-02-01 принята", out)

    def test_X02_bridge_reads_base(self):
        self.run_kb("code-kit", str(self.code))
        code, out = self.kbx("similar", "первое уведомление должнику")
        self.assertEqual(code, 0, out)
        self.assertIn("PM-02", out)
        code, out = self.kbx("fact", "PM-02", "Вход", "x")
        self.assertEqual(code, 1)

    def test_X03_ci_fails_on_kb_folder(self):
        self.run_kb("code-kit", str(self.code))
        subprocess.run(["git", "init", "-q", str(self.code)], check=True)
        (self.code / "kb").mkdir()
        (self.code / "kb" / "team.md").write_text("x\n")
        subprocess.run(["git", "-C", str(self.code), "add", "-A"], check=True)
        r = subprocess.run(["sh", "ci-no-kb.sh"], cwd=self.code, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("в ветке кода есть папка kb/", r.stdout)
        subprocess.run(["git", "-C", str(self.code), "rm", "-rq", "--cached", "kb"], check=True)
        shutil.rmtree(self.code / "kb")
        r = subprocess.run(["sh", "ci-no-kb.sh"], cwd=self.code, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_X04_code_agent_permissions(self):
        self.run_kb("code-kit", str(self.code))
        cfg = json.loads((self.code / "opencode.json").read_text(encoding="utf-8"))
        perm = cfg["permission"]
        self.assertEqual(decide(perm["bash"], "git push origin develop"), "deny")
        self.assertEqual(decide(perm["bash"], "git commit -m x"), "ask")
        self.assertEqual(decide(perm["bash"], "cat p7s-config/values/production.yaml"), "deny")
        self.assertEqual(decide(perm["read"], "p7s-config/values/production.yaml"), "deny")
        self.assertEqual(decide(perm["edit"], "build.gradle"), "ask")
        self.assertEqual(decide(perm["edit"], "kbx.py"), "deny")
        self.assertFalse(cfg["tools"]["*merge*"])
        self.assertTrue(cfg["agent"]["general"]["disable"])

    def test_X05_refuses_code_folder_with_kb(self):
        (self.code / "kb").mkdir()
        code, out = self.run_kb("code-kit", str(self.code))
        self.assertEqual(code, 1)
        self.assertIn("в папке кода есть kb/", out)


class Mock(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        Mock.seen.append((self.path, dict(self.headers), body))
        if self.path == "/api/chat/completions":
            answer = "<think>подумаю</think>{'event': 'status'} Срок хранения — 3 года."
            data = json.dumps({"choices": [{"message": {"content": answer}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/api/rag/conversations/c1/messages/"):
            events = [{"type": "start"}, {"type": "text-delta", "delta": "Сервис "}, {"type": "text-delta", "delta": "уведомлений."},
                      {"type": "finish"}]
            data = ("".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            data = b"{}"
            self.send_response(404)
        self.end_headers()
        self.wfile.write(data)


class Rag(Base):
    def setUp(self):
        super().setUp()
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.server.server_port}"
        self.saved = {k: os.environ.get(k) for k in ("KB_OPENWEBUI_URL", "KB_OPENWEBUI_KEY", "KB_PORTAL_URL",
                                                     "KB_PORTAL_COOKIE", "NO_PROXY", "no_proxy")}
        os.environ.update({"KB_OPENWEBUI_URL": url + "/", "KB_OPENWEBUI_KEY": "sekret-key", "KB_PORTAL_URL": url,
                           "KB_PORTAL_COOKIE": "session=sekret-cookie", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        Mock.seen = []

    def tearDown(self):
        self.server.shutdown()
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_R01_openwebui_needs_human_choice(self):
        code, out = self.run_kb("rag", "openwebui", "--vopros", "Срок хранения журнала?")
        self.assertEqual(code, 1)
        self.assertIn("какой RAG — решает человек", out)

    def test_R02_openwebui_answer_cleaned_and_saved(self):
        code, out = self.run_kb("rag", "openwebui", "--rag", "idcrag_pipeline", "--vopros", "Срок хранения журнала?",
                                "--save", "srok-hraneniya")
        self.assertEqual(code, 0, out)
        self.assertIn("Срок хранения — 3 года.", out)
        self.assertNotIn("think", out)
        self.assertNotIn("event", out)
        path, headers, body = Mock.seen[0]
        self.assertEqual(path, "/api/chat/completions")
        self.assertEqual(body["model"], "idcrag_pipeline")
        self.assertEqual(headers["Authorization"], "Bearer sekret-key")
        self.assertNotIn("sekret", out)
        saved = (self.root / "materials/ai/2026-10-18-srok-hraneniya.md").read_text(encoding="utf-8")
        self.assertIn("Ответ ИИ — не факт", saved)
        code, out = self.run_kb("fact", "PM-02", "Выход", "- Журнал хранят 3 года (ист: materials/ai/2026-10-18-srok-hraneniya.md:9; подтверждено)")
        self.assertEqual(code, 1)
        self.assertIn("не выше «оценка»", out)

    def test_R03_portal_stream(self):
        code, out = self.run_kb("rag", "portal", "--proekt", "notify-service", "--chat", "c1", "--vopros", "Кто шлёт SMS?")
        self.assertEqual(code, 0, out)
        self.assertIn("Сервис уведомлений.", out)
        path, headers, body = Mock.seen[0]
        self.assertEqual(body["metadata"]["custom"]["navigationContext"]["projectName"], "notify-service")
        self.assertEqual(headers["Cookie"], "session=sekret-cookie")
        self.assertNotIn("sekret", out)

    def test_R04_portal_needs_project(self):
        code, out = self.run_kb("rag", "portal", "--chat", "c1", "--vopros", "x")
        self.assertEqual(code, 1)
        self.assertIn("какой проект — решает человек", out)

    def test_R05_missing_secret_not_leaked(self):
        os.environ.pop("KB_PORTAL_COOKIE")
        code, out = self.run_kb("rag", "portal", "--proekt", "p", "--chat", "c1", "--vopros", "x")
        self.assertEqual(code, 1)
        self.assertIn("не задана переменная окружения KB_PORTAL_COOKIE", out)


if __name__ == "__main__":
    unittest.main()
