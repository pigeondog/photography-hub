#!/usr/bin/env python3
"""Check project documentation using only the Python 3.9 standard library."""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit


REQUIRED_DOCUMENTS = ("AGENTS.md", "PROJECT_SPEC.md", "docs/DEVELOPMENT.md")
REQUIRED_SECTIONS = (
    "变更目的与行为差异",
    "影响范围与设计",
    "验收场景",
    "验证计划与结果",
    "清理与兼容",
    "风险与恢复",
)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}##\s+(.+?)(?:\s+#+)?\s*$")
AC_REFERENCE = re.compile(r"\bAC-\d+\b")
AC_DEFINITION = re.compile(r"^\|\s*(AC-\d+)\s*\|", re.MULTILINE)
PLACEHOLDER = re.compile(
    r"^\s*(?:[-*]\s*)?(?:TODO|TBD|待填写|待补充|<填写[^>]*>)\s*[。.!！]?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# A deliberately small Markdown subset: inline links and reference definitions.
# Destinations may use angle brackets, escapes, or one nested parenthesis pair.
DESTINATION = r"(?P<target><[^>\n]*>|(?:\\.|[^\s()\\]|\([^()\n]*\))+)"
TITLE = r'''(?:\s+(?:"[^"\n]*"|'[^'\n]*'|\([^\n)]*\)))?'''
INLINE_LINK = re.compile(
    r"!?\[(?:\\.|[^\]\\\n])*\]\(\s*" + DESTINATION + TITLE + r"\s*\)"
)
REFERENCE_LINK = re.compile(
    r"^ {0,3}\[[^\]\n]+\]:\s*" + DESTINATION + TITLE + r"\s*$"
)


@dataclass(frozen=True)
class Problem:
    path: Path
    line: int
    message: str


def within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def prose_lines(text: str) -> List[str]:
    """Hide comments and fenced code, preserving line numbers for diagnostics."""
    text = re.sub(
        r"<!--.*?-->",
        lambda match: re.sub(r"[^\n]", " ", match.group()),
        text,
        flags=re.DOTALL,
    )
    lines = []
    fence_char = ""
    fence_length = 0
    for line in text.splitlines():
        match = FENCE.match(line)
        if fence_char:
            if (
                match
                and match[1][0] == fence_char
                and len(match[1]) >= fence_length
                and not match[2].strip()
            ):
                fence_char = ""
            lines.append("")
        elif match:
            fence_char = match[1][0]
            fence_length = len(match[1])
            lines.append("")
        else:
            lines.append(line)
    return lines


def local_link_problems(root: Path, path: Path, text: str) -> List[Problem]:
    problems = []
    for number, line in enumerate(prose_lines(text), start=1):
        line = re.sub(r"(`+).*?\1", "", line)
        matches = list(INLINE_LINK.finditer(line))
        reference = REFERENCE_LINK.match(line)
        if reference:
            matches.append(reference)
        for match in matches:
            target = match["target"]
            if target.startswith("<"):
                target = target[1:-1]
            target = re.sub(r"\\([\\`*{}\[\]()#+\-.!_<> ])", r"\1", target)
            if target.startswith("#") or target.startswith("//"):
                continue
            scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", target)
            if scheme:
                if scheme[1].lower() == "file":
                    problems.append(Problem(path, number, "本地链接请使用项目内路径，不使用 file: URL"))
                continue
            try:
                raw_path = unquote(urlsplit(target).path)
                if not raw_path:
                    continue
                candidate = root / raw_path.lstrip("/") if raw_path.startswith("/") else path.parent / raw_path
                candidate = candidate.resolve()
                if not within(candidate, root):
                    problems.append(Problem(path, number, "本地链接越出项目目录：" + target))
                elif not candidate.is_file():
                    problems.append(Problem(path, number, "本地链接未指向有效文件：" + target))
            except (OSError, ValueError) as error:
                problems.append(Problem(path, number, "无法解析本地链接 {!r}：{}".format(target, error)))
    return problems


def has_content(text: str) -> bool:
    return bool(re.search(r"[^\W_]", text))


def change_problems(path: Path, text: str, known_acs: set) -> List[Problem]:
    problems = []
    lines = prose_lines(text)
    headings = [(index, match[1]) for index, line in enumerate(lines) if (match := HEADING.match(line))]
    sections: Dict[str, Tuple[int, str]] = {}
    for position, (index, title) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        if title in sections:
            problems.append(Problem(path, index + 1, "重复栏目：" + title))
        else:
            sections[title] = (index + 1, "\n".join(lines[index + 1:end]))

    for title in REQUIRED_SECTIONS:
        if title not in sections:
            problems.append(Problem(path, 1, "缺少必需栏目：## " + title))
            continue
        number, body = sections[title]
        if not has_content(body):
            problems.append(Problem(path, number, "栏目内容不能为空：" + title))
        elif PLACEHOLDER.search(body):
            problems.append(Problem(path, number, "栏目含未替换的占位行：" + title))

    for number, line in enumerate(lines, start=1):
        for reference in AC_REFERENCE.finditer(line):
            if reference[0] not in known_acs:
                problems.append(Problem(path, number, "需求基线未定义验收编号：" + reference[0]))

    if "验收场景" in sections:
        number, body = sections["验收场景"]
        references = set(AC_REFERENCE.findall(body))
        reason = re.search(r"不适用[：:]\s*([^\n]+)", body)
        explained = reason and has_content(reason[1]) and not PLACEHOLDER.fullmatch(reason[1])
        if not references and not explained:
            problems.append(Problem(path, number, "验收场景须引用 AC 编号，或填写“不适用：具体原因”"))
    return problems


def check_project(root: Path, change: Optional[Path] = None) -> List[Problem]:
    root = root.resolve()
    problems = []
    required = [root / name for name in REQUIRED_DOCUMENTS]
    documents = set(required) | set((root / "docs").rglob("*.md"))
    texts = {}
    for path in sorted(documents):
        if not within(path.resolve(), root):
            problems.append(Problem(path, 1, "受管文档不能指向项目目录之外"))
            continue
        if not path.is_file():
            problems.append(Problem(path, 1, "缺少必需文档"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            problems.append(Problem(path, 1, "无法读取 UTF-8 文档：" + str(error)))
            continue
        texts[path.resolve()] = text
        if path in required and not has_content("\n".join(prose_lines(text))):
            problems.append(Problem(path, 1, "必需文档不能为空"))
        problems.extend(local_link_problems(root, path, text))

    if change is not None:
        path = (root / change).resolve()
        if not within(path, root / "docs" / "changes") or path.suffix != ".md":
            problems.append(Problem(path, 1, "变更记录必须是 docs/changes/ 下的 Markdown 文件"))
        elif path not in texts:
            problems.append(Problem(path, 1, "变更记录不存在或无法读取"))
        else:
            baseline = texts.get(root / "PROJECT_SPEC.md", "")
            known_acs = set(AC_DEFINITION.findall("\n".join(prose_lines(baseline))))
            if not known_acs:
                problems.append(Problem(root / "PROJECT_SPEC.md", 1, "未找到验收表中的 AC 编号定义"))
            problems.extend(change_problems(path, texts[path], known_acs))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--change", type=Path, help="检查 docs/changes/ 下的指定变更记录")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    problems = check_project(root, arguments.change)
    for problem in problems:
        path = problem.path.relative_to(root) if within(problem.path, root) else problem.path
        print("{}:{}: {}".format(path, problem.line, problem.message))
    if problems:
        print("文档检查失败：{} 个问题。".format(len(problems)))
    else:
        print("文档检查通过：必需文档及本地文件链接。")
        if arguments.change is not None:
            print("变更记录检查通过：栏目、内容及验收编号引用。")
    print("代码检查：尚未配置。本入口仅执行文档检查，工程建立时须接入真实代码检查。")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
