"""Source-level rules for the engine package: no network (003/AC5), UTF-8 everywhere (003/R5, P10), registry hygiene."""
import ast
import re
from pathlib import Path


ENGINE = Path(__file__).resolve().parents[2] / "tooling" / "engine"
PACKAGE = ENGINE / "metarepo_engine"

# 003/R14: network access is allowed only for `repos clone` (a git subprocess) and for cloud extractors that
# the project explicitly enabled (008). Those modules, once they exist, are listed here and nowhere else.
NETWORK_ALLOWED = {
    "repos.py",
    "commands/repos_clone.py",
}
NETWORK_ALLOWED_PREFIXES = ("extract/adapters/cloud",)

NETWORK_PATTERNS = [
    r"^\s*(?:import|from)\s+(?:socket|ssl|ftplib|smtplib|poplib|imaplib|telnetlib|xmlrpc|http\.client|http\.server|"
    r"urllib\.request|urllib3|requests|httpx|aiohttp|websocket|websockets|paramiko)\b",
    r"\burlopen\b", r"\bsocket\.", r"\bcreate_connection\b", r"\bopen_connection\b",
    r"""["'](?:curl|wget|ssh|scp|ftp|nc|ncat)["']""",
]


def engine_sources():
    for path in sorted(PACKAGE.rglob("*.py")) + [ENGINE / "cli.py"]:
        if "__pycache__" not in path.parts:
            yield path


def allowed_network(path: Path) -> bool:
    rel = path.relative_to(PACKAGE).as_posix() if PACKAGE in path.parents else path.name
    return rel in NETWORK_ALLOWED or rel.startswith(NETWORK_ALLOWED_PREFIXES)


def test_ac5_no_network_calls_outside_the_two_allowed_paths():
    offenders = []
    for path in engine_sources():
        if allowed_network(path):
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in NETWORK_PATTERNS:
            for m in re.finditer(pattern, text, re.M):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(ENGINE)}:{line}: {m.group(0).strip()}")
    assert offenders == []


def test_the_network_audit_detects_offenders(tmp_path):
    bad = "import socket\nimport urllib.request\nx = urllib.request.urlopen('http://example.com')\n"
    hits = [p for p in NETWORK_PATTERNS if re.search(p, bad, re.M)]
    assert len(hits) >= 2
    assert re.search(NETWORK_PATTERNS[-1], "subprocess.run(['curl', 'x'])")
    assert not any(re.search(p, "from urllib.parse import unquote\nimport subprocess\n", re.M) for p in NETWORK_PATTERNS)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _mode_of(node: ast.Call, positional_index: int) -> str | None:
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    if len(node.args) > positional_index and isinstance(node.args[positional_index], ast.Constant):
        return node.args[positional_index].value
    return None


def test_text_file_io_always_names_its_encoding():
    """read_text / write_text / text-mode open() must pass encoding= (003/R5): never the platform default."""
    problems = []
    for path in engine_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            has_encoding = any(kw.arg == "encoding" for kw in node.keywords)
            if name in ("read_text", "write_text") and not has_encoding:
                problems.append(f"{path.relative_to(ENGINE)}:{node.lineno}: {name}() without encoding")
            if name == "open":
                is_builtin_open = isinstance(node.func, ast.Name) or (
                    isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ("io", "codecs"))
                if not is_builtin_open:
                    continue
                if isinstance(node.func, ast.Attribute) and node.func.value.id == "os":
                    continue
                mode = _mode_of(node, 1)
                if mode is not None and "b" in mode:
                    continue
                if not has_encoding:
                    problems.append(f"{path.relative_to(ENGINE)}:{node.lineno}: open() in text mode without encoding")
    assert problems == []


def test_every_command_module_is_registered_and_vice_versa():
    from metarepo_engine.commands import MODULES

    on_disk = {p.stem for p in (PACKAGE / "commands").glob("*.py") if not p.name.startswith("_") and p.stem != "__init__"}
    assert on_disk == set(MODULES)


def test_command_modules_define_exactly_one_command_and_stubs_use_the_helper():
    for path in (PACKAGE / "commands").glob("*.py"):
        if path.name.startswith("_") or path.stem == "__init__":
            continue
        text = path.read_text(encoding="utf-8")
        assert len(re.findall(r"^COMMAND\s*=", text, re.M)) == 1, path.name


def test_contributing_guide_documents_the_registry_pattern():
    guide = (ENGINE / "CONTRIBUTING-commands.md").read_text(encoding="utf-8")
    for needle in ("cli.py", "MODULES", "COMMAND", "replace the stub", "writes_project", "dry_run", "Result"):
        assert needle in guide
    assert not re.search(r"[^\x00-\x7f]", guide)  # English, plain ASCII


def test_runtime_dependency_is_only_pyyaml():
    third_party = set()
    stdlib = __import__("sys").stdlib_module_names
    for path in engine_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            third_party |= {n for n in names if n not in stdlib and n != "metarepo_engine"}
    assert third_party == {"yaml"}
    assert (ENGINE / "requirements.txt").read_text(encoding="utf-8").count("PyYAML") == 1
