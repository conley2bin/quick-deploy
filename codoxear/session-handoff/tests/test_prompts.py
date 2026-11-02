import importlib
from pathlib import Path
import shlex
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if "handoff_test" not in sys.modules:
    package = types.ModuleType("handoff_test")
    package.__path__ = [str(ROOT)]
    sys.modules["handoff_test"] = package
prompts = importlib.import_module("handoff_test.prompts")


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="prompt space '")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manifest = {"operation_id": "op-marker", "note": "用户备注：保留记录"}

    def test_recovery_only_recovers_and_reports_then_waits_for_review(self):
        prompt = prompts.build_recovery_prompt(self.manifest, self.directory)
        self.assertIn("仅独立恢复与报告", prompt)
        self.assertIn("不修改项目文件", prompt)
        self.assertIn("不是等待人工确认", prompt)
        self.assertIn("--kind understanding", prompt)
        self.assertNotIn("--kind review", prompt)
        self.assertIn("不得按最新 mtime 猜任务", prompt)
        self.assertIn("侧分支不是当前目标", prompt)

    def test_old_session_stays_reviewer(self):
        prompt = prompts.build_review_prompt(self.manifest, self.directory, "a" * 64)
        self.assertIn("只审阅，不继续原任务", prompt)
        self.assertIn("a" * 64, prompt)
        self.assertIn("corrections", prompt)
        self.assertIn("conflict", prompt)
        self.assertIn("不开启第二轮互审", prompt)

    def test_continuation_checks_objections_once_receipt_before_execution(self):
        prompt = prompts.build_continuation_prompt(self.manifest, self.directory, "b" * 64)
        self.assertIn("b" * 64, prompt)
        self.assertIn("逐条以当前证据核对", prompt)
        self.assertIn("最多这一轮", prompt)
        self.assertIn("只有 CLI 成功写入 ready 回执后", prompt)
        self.assertIn("verdict=conflict", prompt)
        self.assertNotIn(" --unreviewed", prompt)
        bypass = prompts.build_continuation_prompt(self.manifest, self.directory, "unreviewed", unreviewed=True)
        self.assertIn("用户已显式选择", bypass)
        self.assertIn(" --unreviewed", bypass)

    def test_conflict_review_requires_verification_not_blind_obedience(self):
        prompt = prompts.build_continuation_prompt(self.manifest, self.directory, "b" * 64)
        self.assertIn("review.verdict=conflict", prompt)
        self.assertIn("verification-only", prompt)
        self.assertIn("尚不授权执行任务", prompt)
        self.assertIn("只有最新明确用户指令或当前直接证据", prompt)
        self.assertIn("无法消除则写 conflict 并停止", prompt)
        self.assertIn("旧意见也不能授予任意新任务或新任命权限", prompt)

    def test_shared_evidence_authority_and_safe_argv(self):
        values = [prompts.build_recovery_prompt(self.manifest, self.directory),
                  prompts.build_review_prompt(self.manifest, self.directory, "a" * 64),
                  prompts.build_continuation_prompt(self.manifest, self.directory, "b" * 64)]
        for prompt in values:
            self.assertNotIn(prompt[0], "-@")
            self.assertIn("不转移负责人权限", prompt)
            self.assertIn("不得批量重放历史命令", prompt)
            self.assertIn("当前文件/测试等直接证据优先", prompt)
            self.assertIn("latest_committed", prompt)
            self.assertIn("cutoff_bytes", prompt)
            command = next(line for line in prompt.splitlines() if " submit --manifest " in line)
            argv = shlex.split(command)
            self.assertNotIn("--", argv)
            self.assertEqual(argv[1], str(ROOT / "handoff_io.py"))
            self.assertEqual(argv[argv.index("--manifest") + 1], str(self.directory / "manifest.json"))
            self.assertEqual(Path(argv[argv.index("--body-file") + 1]).parent, self.directory)

    def test_installed_module_location_not_developer_path(self):
        with patch.object(prompts, "__file__", "/opt/installed/session-handoff/prompts.py"):
            prompt = prompts.build_recovery_prompt(self.manifest, self.directory)
        self.assertIn("/opt/installed/session-handoff/handoff_io.py", prompt)
        self.assertNotIn(str(ROOT / "handoff_io.py"), prompt)


if __name__ == "__main__":
    unittest.main()
