"""Порция 6: штурман kb where и агенты OpenCode со своей моделью на каждого."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_steps import Base  # noqa: E402

AGENT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG = json.loads((AGENT / "opencode.json").read_text(encoding="utf-8"))


class Where(Base):
    def test_owner_with_gaps(self):
        code, out = self.run_kb("where")
        self.assertEqual(code, 0, out)
        self.assertIn("Ты здесь: PM-02 Первое уведомление должника — «Разбор» · ждёт ответов: 1 (Q-001)", out)
        self.assertIn("Дальше: разобрать «Состояния»", out)
        self.assertIn("/razbor PM-02", out)
        self.assertIn("модель: dialog", out)

    def test_owner_without_gaps_decides_enough(self):
        path = self.root / "context/process/PM-02-pervoe-uvedomlenie.md"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "## Состояния\n", "## Состояния\n- Должник уведомлён (ист: materials/2026-10-01-reglament.md:9; подтверждено)\n"),
            encoding="utf-8")
        code, out = self.run_kb("where")
        self.assertIn("«мне достаточно» — kb step enough", out)
        self.assertIn("готовность решает владелец", out)
        self.run_kb("step", "enough", "PM-02")
        code, out = self.run_kb("where")
        self.assertIn('Дальше: в сверку с бизнесом', out)

    def test_developer_without_steps_sees_free(self):
        self.new_step("PM-05", "Готов к взятию")
        self.as_person("Разработчик-2")
        code, out = self.run_kb("where")
        self.assertIn("своих шагов в работе нет", out)
        self.assertIn("Свободны: PM-05", out)

    def test_analyst_sees_unsorted(self):
        self.run_kb("ask", "send", "x", "шаг=PM-02", "кто=бизнес", "откуда=materials/2026-10-01-zapros.md:7",
                    "вопрос=Сколько стоит SMS?")
        self.as_person("Аналитик")
        code, out = self.run_kb("where")
        self.assertIn("новых вопросов без сортировки — 1 (Q-new-x)", out)

    def test_architect_sees_acceptance(self):
        self.as_person("Разработчик-2")
        code, out = self.run_kb("where")
        self.assertIn("Архитектору недели: ждут приёмки — нет", out)


class Agents(unittest.TestCase):
    agents = CONFIG["agent"]

    def test_every_agent_model_from_env(self):
        for name, agent in self.agents.items():
            if agent.get("disable"):
                continue
            self.assertRegex(agent["model"], r"^\{env:KB_MODEL_[A-Z_]+\}$", name)

    def test_heavy_model_only_in_subagents_with_step_limit(self):
        for name, agent in self.agents.items():
            if agent.get("model") == "{env:KB_MODEL_HEAVY}":
                self.assertEqual(agent["mode"], "subagent", name)
                self.assertLessEqual(agent["steps"], 10, name)

    def test_subagents_read_only(self):
        for name, agent in self.agents.items():
            if agent.get("mode") == "subagent":
                perm = agent["permission"]
                self.assertEqual(perm["edit"], "deny", name)
                self.assertEqual(perm["bash"]["*"], "deny", name)
                for pattern, action in perm["bash"].items():
                    if action == "allow":
                        self.assertNotRegex(pattern, r"commit|ask send|fact|q |step enough|step status|take", name)

    def test_prompts_exist(self):
        for name, agent in self.agents.items():
            if "prompt" in agent:
                path = re.match(r"^\{file:\./(.+)\}$", agent["prompt"]).group(1)
                self.assertTrue((AGENT / path).is_file(), path)

    def test_commands_point_to_known_agents(self):
        for name, cmd in CONFIG["command"].items():
            if "agent" in cmd:
                self.assertIn(cmd["agent"], self.agents, name)
            else:
                self.assertRegex(cmd["model"], r"^\{env:KB_MODEL_[A-Z_]+\}$", name)

    def test_builtin_general_disabled(self):
        self.assertTrue(self.agents["general"]["disable"])
        self.assertEqual(CONFIG["default_agent"], "kb")

    def test_mcp_merge_and_approve_off(self):
        # Слить или одобрить MR через MCP GitLab — никогда: инструменты выключены по шаблону имени
        self.assertIs(CONFIG["tools"]["*merge*"], False)
        self.assertIs(CONFIG["tools"]["*approve*"], False)


if __name__ == "__main__":
    unittest.main()
