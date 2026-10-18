"""Стенд (условие 4 цели): хорошая поддельная модель проходит всё, плохая проваливает каждый сценарий."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent / "eval"
sys.path.insert(0, str(EVAL))

import run  # noqa: E402


class Stand(unittest.TestCase):
    def test_good_model_passes_every_scenario(self):
        for sid in run.SCENARIOS:
            with self.subTest(sid=sid):
                res = run.fake_run("good", sid)
                self.assertTrue(res["passed"], json.dumps(res, ensure_ascii=False, indent=1))

    def test_bad_model_fails_every_scenario(self):
        for sid in run.SCENARIOS:
            with self.subTest(sid=sid):
                res = run.fake_run("bad", sid)
                self.assertFalse(res["passed"], f"{sid}: плохая модель прошла")

    def test_mandatory_scenarios_present(self):
        titles = " ".join(s["title"] for s in run.SCENARIOS.values())
        for need in ("без «да»", "«задаём»", "кто сказал", "разобранным", "выдумывает", "отчёт", "встречи", "пушит"):
            self.assertIn(need, titles)

    def test_every_scenario_has_gate_and_behavior_checks(self):
        for sid, sc in run.SCENARIOS.items():
            kinds = {c["kind"] for c in sc["checks"] + run.common_checks(sc)}
            self.assertEqual(kinds, {"gate", "behavior"}, sid)


class Shell(unittest.TestCase):
    def test_sh_follows_opencode_rules(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"
            run.prepare("E1", work)
            def sh(cmd, approve="no"):
                (work / ".eval/approve").write_text(approve)
                return subprocess.run([sys.executable, "kb/tools/eval/sh.py", cmd], cwd=work, capture_output=True, text=True)
            self.assertEqual(sh("git push").returncode, 126)
            self.assertIn("запрещено правами OpenCode", sh("git commit -m x").stdout)
            self.assertIn("человек отказал", sh("python3 kb/tools/kb.py commit apply abcdef").stdout)
            self.assertEqual(sh("python3 kb/tools/kb.py check").returncode, 0)
            log = (work / ".eval/commands.log").read_text().splitlines()
            self.assertEqual(len(log), 4)


if __name__ == "__main__":
    unittest.main()
