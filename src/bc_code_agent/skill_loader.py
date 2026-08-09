"""简易 Skill 加载器：渐进式披露。

1. system 里只放目录（name + description）
2. 需要时再 LoadSkill(name) 取完整正文
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---
# name: baichen
# description: a simple skill loader
# ---
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    dir: Path  # skill 根目录：可含 reference.md / assets / scripts
    skill_md: Path  # SKILL.md 路径


def _parse_frontmatter(raw: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("'\"")
    return meta


def parse_skill_md(skill_md: Path) -> Skill | None:
    text = skill_md.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        print(f"[Skill] skip (no frontmatter): {skill_md}")
        return None

    meta = _parse_frontmatter(match.group(1))
    name = meta.get("name") or skill_md.parent.name
    description = meta.get("description", "")
    body = match.group(2).strip()
    return Skill(
        name=name,
        description=description,
        body=body,
        dir=skill_md.parent,
        skill_md=skill_md,
    )


class SkillLoader:
    """加载 `skills_dir/<skill-name>/SKILL.md`。"""

    def __init__(self, skills_dir: str | Path) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills: list[Skill] = []
        self._by_name: dict[str, Skill] = {}

    def load(self) -> list[Skill]:
        self.skills = []
        self._by_name = {}
        if not self.skills_dir.is_dir():
            print(f"[Skill] dir not found: {self.skills_dir}")
            return self.skills

        for skill_md in sorted(self.skills_dir.glob("*/SKILL.md")):
            skill = parse_skill_md(skill_md)
            if skill is None:
                continue
            self.skills.append(skill)
            self._by_name[skill.name] = skill
            print(f"[Skill] indexed: {skill.name} ({skill_md})")

        return self.skills

    def catalog_prompt(self) -> str:
        """只暴露目录（name + description），不塞正文。"""
        if not self.skills:
            return ""

        parts = [
            "# Available Skills",
            "下面只有 Skill 目录。若 description 与当前任务匹配：",
            "1. 先调用工具 `LoadSkill`，传入 skill 的 name",
            "2. 再严格按返回的完整 Instructions 执行",
            "不要在未 LoadSkill 时臆造 Skill 细节。",
            "",
        ]
        for skill in self.skills:
            parts.append(f"- `{skill.name}`: {skill.description}")
        parts.append("")
        return "\n".join(parts)

    def view(self, name: str) -> str:
        """按需返回 SKILL.md 正文，并附带目录内其它文本资源（reference / scripts 等）。"""
        skill = self._by_name.get(name)
        if skill is None:
            known = ", ".join(sorted(self._by_name)) or "(none)"
            return f"Skill not found: {name!r}. Known: {known}"

        parts = [
            f"# Skill: `{skill.name}`",
            f"Description: {skill.description}",
            f"Dir: {skill.dir}",
            "",
            skill.body,
        ]

        for path in sorted(skill.dir.rglob("*")):
            if not path.is_file():
                continue
            if path.resolve() == skill.skill_md.resolve():
                continue
            rel = path.relative_to(skill.dir)
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                parts.extend(["", f"## Resource: `{rel}`", "(binary skipped)"])
                continue
            parts.extend(["", f"## Resource: `{rel}`", content])

        return "\n".join(parts).rstrip() + "\n"

    # 兼容旧名
    def to_prompt(self) -> str:
        return self.catalog_prompt()
