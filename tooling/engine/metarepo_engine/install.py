"""``install-skills`` (001/R6): copy the portable skills into a harness skills directory.

Each installed skill gets its ``references/engine.md`` marker block pointing at the absolute
path of this engine's ``cli.py``. Nothing is ever written into a metarepository.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .fsops import atomic_write_bytes
from .resolve import MARKER_END, MARKER_START
from .root import find_root

ENGINE_MD_REL = "references/engine.md"
_IGNORED = {"__pycache__", ".DS_Store"}


class InstallError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def engine_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def skills_source() -> Path:
    return engine_dir().parent / "skills"


def set_engine_path(text: str, path: str) -> str:
    """Replace the content of the engine-path marker block with ``path: <path>``."""
    start, end = text.find(MARKER_START), text.find(MARKER_END)
    if start < 0 or end < start:
        raise InstallError("engine_md_malformed", "references/engine.md has no engine-path marker block")
    head = text[: start + len(MARKER_START)]
    tail = text[end:]
    nl = "\r\n" if "\r\n" in text else "\n"
    return f"{head}{nl}path: {path}{nl}{tail}"


@dataclass
class SkillPlan:
    name: str
    action: str  # install | unchanged | update
    files: dict[str, bytes]


def _iter_files(skill_dir: Path):
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file() and not (set(p.relative_to(skill_dir).parts) & _IGNORED) and p.suffix != ".pyc":
            yield p


def plan_install(target: Path, source: Path | None = None, engine_cli: Path | None = None) -> list[SkillPlan]:
    source = source or skills_source()
    engine_cli = engine_cli or (engine_dir() / "cli.py")
    if not source.is_dir():
        raise InstallError("skills_missing", f"no skills directory at {source}")
    plans: list[SkillPlan] = []
    for skill in sorted(p for p in source.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
        files: dict[str, bytes] = {}
        for f in _iter_files(skill):
            rel = f.relative_to(skill).as_posix()
            data = f.read_bytes()
            if rel == ENGINE_MD_REL:
                data = set_engine_path(data.decode("utf-8"), str(engine_cli)).encode("utf-8")
            files[rel] = data
        dest = target / skill.name
        if not dest.exists():
            action = "install"
        else:
            same = all((dest / rel).is_file() and (dest / rel).read_bytes() == data for rel, data in files.items())
            action = "unchanged" if same else "update"
        plans.append(SkillPlan(skill.name, action, files))
    return plans


def install_skills(target: Path, *, force: bool = False, dry_run: bool = False,
                   source: Path | None = None, engine_cli: Path | None = None) -> list[SkillPlan]:
    target = Path(target).expanduser().resolve()
    if find_root(target if target.exists() else target.parent) is not None:
        raise InstallError("target_inside_project",
                           f"{target} is inside a metarepository; the toolkit is never installed into a project")
    plans = plan_install(target, source, engine_cli)
    conflicts = [p.name for p in plans if p.action == "update"]
    if conflicts and not force:
        raise InstallError(
            "skill_exists",
            "these skills already exist with different content: " + ", ".join(conflicts)
            + " (re-run with --force to overwrite them)",
        )
    if dry_run:
        return plans
    for plan in plans:
        if plan.action == "unchanged":
            continue
        for rel, data in plan.files.items():
            atomic_write_bytes(target / plan.name / rel, data)
    return plans
