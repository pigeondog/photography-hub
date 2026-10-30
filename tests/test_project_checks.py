"""Behavioral tests for documentation checks; all fixtures use temporary files."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.check_project import REQUIRED_SECTIONS, check_project


class ProjectCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.write("AGENTS.md", "# 项目约束\n")
        self.write("docs/DEVELOPMENT.md", "# 开发指南\n")
        self.write(
            "PROJECT_SPEC.md",
            "# 基线\n\n| 编号 | 场景 |\n|---|---|\n| AC-01 | 原文保留 |\n| AC-02 | 文件保留 |\n",
        )
        self.record = Path("docs/changes/2026-10-30-example.md")
        self.bodies = {
            "变更目的与行为差异": "修改原文展示，保存行为不变。",
            "影响范围与设计": "只修改原文组件，不改变接口和模块边界。",
            "验收场景": "AC-01：检查标点和换行保持原样。",
            "验证计划与结果": "已用独立样本核对显示结果，原文未变。",
            "清理与兼容": "删除重复展示逻辑，无兼容分支。",
            "风险与恢复": "无数据迁移；可撤回组件修改。",
        }

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_record(self, bodies=None):
        bodies = self.bodies if bodies is None else bodies
        return self.write(
            self.record,
            "# 示例变更\n\n" + "\n\n".join("## {}\n\n{}".format(title, body) for title, body in bodies.items()),
        )

    def messages(self, change=None):
        return "\n".join(problem.message for problem in check_project(self.root, change))

    def test_default_check_accepts_complete_documents(self):
        self.assertEqual(check_project(self.root), [])

    def test_missing_required_document_is_reported(self):
        (self.root / "AGENTS.md").unlink()
        problems = check_project(self.root)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].path.name, "AGENTS.md")
        self.assertIn("缺少必需文档", problems[0].message)

    def test_empty_required_document_is_reported(self):
        self.write("AGENTS.md", "<!-- 无实际内容 -->\n___\n")
        self.assertIn("必需文档不能为空", self.messages())

    def test_valid_record_accepts_absolute_and_relative_paths(self):
        absolute = self.write_record()
        self.assertEqual(check_project(self.root, self.record), [])
        self.assertEqual(check_project(self.root, absolute), [])

    def test_missing_or_duplicate_section_is_reported(self):
        bodies = dict(self.bodies)
        del bodies["清理与兼容"]
        self.write_record(bodies)
        self.assertIn("缺少必需栏目：## 清理与兼容", self.messages(self.record))
        path = self.write_record()
        path.write_text(path.read_text(encoding="utf-8") + "\n## 清理与兼容\n\n重复内容。\n", encoding="utf-8")
        self.assertIn("重复栏目：清理与兼容", self.messages(self.record))

    def test_empty_section_and_template_placeholders_are_rejected(self):
        for value in ("", "___", "<!-- 仅注释 -->", "待填写", "- TODO", "TBD"):
            with self.subTest(value=value):
                self.write_record(dict(self.bodies, **{"影响范围与设计": value}))
                self.assertTrue(check_project(self.root, self.record))

    def test_unknown_ac_reports_file_and_line(self):
        self.write_record(dict(self.bodies, **{"验收场景": "AC-999：测试不存在的场景。"}))
        problems = check_project(self.root, self.record)
        unknown = [problem for problem in problems if "AC-999" in problem.message]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].path, self.root / self.record)
        self.assertGreater(unknown[0].line, 1)

    def test_unknown_ac_outside_acceptance_section_is_also_rejected(self):
        self.write_record(dict(self.bodies, **{"影响范围与设计": "参考 AC-888 的设计。"}))
        self.assertIn("AC-888", self.messages(self.record))

    def test_ac_mentions_outside_baseline_table_do_not_define_ids(self):
        baseline = self.root / "PROJECT_SPEC.md"
        baseline.write_text(baseline.read_text(encoding="utf-8") + "\n讨论 AC-999，不是验收定义。\n", encoding="utf-8")
        self.write_record(dict(self.bodies, **{"验收场景": "AC-999：未定义。"}))
        self.assertIn("未定义验收编号：AC-999", self.messages(self.record))

    def test_not_applicable_requires_a_reason(self):
        for value in ("不适用", "不适用：", "不适用：   ", "不适用：。", "不适用：TODO", "不适用：___"):
            with self.subTest(value=value):
                self.write_record(dict(self.bodies, **{"验收场景": value}))
                self.assertIn("须引用 AC 编号", self.messages(self.record))
        self.write_record(dict(self.bodies, **{"验收场景": "不适用：本次只调整开发工具，业务行为不变。"}))
        self.assertEqual(check_project(self.root, self.record), [])

    def test_invalid_change_location_or_missing_record_is_rejected(self):
        self.write("docs/not-a-change.md", "# 错误位置\n")
        self.assertIn("必须是 docs/changes/", self.messages(Path("docs/not-a-change.md")))
        self.assertIn("不存在或无法读取", self.messages(self.record))

    def test_broken_link_in_nested_document_reports_its_line(self):
        self.write("docs/nested/guide.md", "# 指南\n\n[失效链接](missing.md)\n")
        problems = check_project(self.root)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].path, self.root / "docs/nested/guide.md")
        self.assertEqual(problems[0].line, 3)

    def test_relative_encoded_image_and_reference_links(self):
        self.write("docs/图片 示例(一).jpg", "fixture")
        self.write(
            "docs/links.md",
            "[基线](../PROJECT_SPEC.md#不存在的锚点)\n"
            "[根目录](/AGENTS.md)\n"
            '![图片](%E5%9B%BE%E7%89%87%20%E7%A4%BA%E4%BE%8B(%E4%B8%80).jpg "标题")\n'
            "[图片](<图片 示例(一).jpg>)\n"
            "[引用][guide]\n[guide]: DEVELOPMENT.md '指南'\n",
        )
        self.assertEqual(check_project(self.root), [])
        self.write("docs/links.md", "[引用][missing]\n[missing]: absent.md\n")
        self.assertIn("absent.md", self.messages())

    def test_external_urls_and_anchors_are_not_fetched(self):
        self.write(
            "docs/links.md",
            "[网页](https://invalid.example/unreachable)\n"
            "[邮件](mailto:example@example.invalid)\n"
            "[协议相对地址](//invalid.example/file.md)\n"
            "[锚点](#不存在的标题)\n",
        )
        self.assertEqual(check_project(self.root), [])

    def test_code_and_comments_are_ignored_without_losing_line_numbers(self):
        self.write(
            "docs/links.md",
            "```markdown\n[示例](absent.md)\n```\n"
            "~~~~text\n[示例](absent.md)\n~~~~\n"
            "`[行内示例](absent.md)`\n"
            "<!--\n[注释](absent.md)\n-->\n"
            "[真实链接](absent.md)\n",
        )
        problems = check_project(self.root)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].line, 11)

    def test_example_headings_in_fences_do_not_satisfy_sections(self):
        self.write_record({"验收场景": "AC-01：有效引用。"})
        path = self.root / self.record
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n```markdown\n"
            + "\n".join("## " + title for title in REQUIRED_SECTIONS)
            + "\n```\n",
            encoding="utf-8",
        )
        self.assertIn("缺少必需栏目", self.messages(self.record))

    def test_outside_paths_and_file_urls_are_rejected(self):
        self.write("docs/links.md", "[越界](../../outside.md)\n[本机](file:///tmp/example.md)\n")
        messages = self.messages()
        self.assertIn("越出项目目录", messages)
        self.assertIn("不使用 file: URL", messages)

    def test_non_utf8_markdown_reports_diagnostic(self):
        (self.root / "docs/broken.md").write_bytes(b"\xff")
        self.assertIn("无法读取 UTF-8 文档", self.messages())

    def test_cli_success_failure_and_argument_exit_codes(self):
        target = self.root / "scripts/check_project.py"
        target.parent.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/check_project.py", target)
        self.write_record()

        def run(*args):
            return subprocess.run(
                [sys.executable, "-B", str(target), *args],
                cwd=self.root.parent,
                capture_output=True,
                text=True,
                check=False,
            )

        success = run("--change", str(self.record))
        self.assertEqual(success.returncode, 0, success.stdout + success.stderr)
        self.assertIn("变更记录检查通过", success.stdout)
        self.assertIn("代码检查：尚未配置", success.stdout)
        self.write("docs/links.md", "[坏链接](missing.md)\n")
        failure = run()
        self.assertEqual(failure.returncode, 1)
        self.assertIn("docs/links.md:1:", failure.stdout)
        self.assertNotIn("文档检查通过", failure.stdout)
        self.assertEqual(run("--unknown").returncode, 2)


if __name__ == "__main__":
    unittest.main()
