"""
@file_name: test_github_skill_layouts.py
@author: Bin Liang
@date: 2026-09-08
@description: GitHub installs accept the same layouts as zip installs, plus
the multi-skill repo layout (GitHub #95). fetch_github_repo used to hardcode
``<clone>/SKILL.md`` while the zip path already used _find_skill_root, so a
repo shipping ``my-skill/SKILL.md`` or ``skills/<name>/SKILL.md`` (the
layout agent-skills / plugin repos use) was rejected as "SKILL.md not found".

The clone itself is stubbed (the ``git clone`` subprocess is replaced by a
function that lays files into dest_dir); everything after it is real.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from narranexus_plugins.skill_module import skill_module as skill_module_mod
from narranexus_plugins.skill_module.skill_module import SkillModule


def _skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name} skill\n---\n# {name}\n"


@pytest.fixture
def module(tmp_path) -> SkillModule:
    sm = SkillModule(agent_id="agent_test", user_id="test_user")
    sm.skills_dir = tmp_path / "skills"
    sm.skills_dir.mkdir(parents=True)
    return sm


def _stub_clone(monkeypatch, layout: dict[str, str]):
    """Replace ``git clone`` with writing ``layout`` ({relpath: content}) into dest."""

    def fake_run(cmd, **kwargs):
        dest = Path(cmd[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()
        for rel, content in layout.items():
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(skill_module_mod.subprocess, "run", fake_run)


URL = "https://github.com/acme/skills-repo"


def test_root_skill_md_installs_one_skill(module, monkeypatch):
    _stub_clone(monkeypatch, {"SKILL.md": _skill_md("root-skill")})

    infos = module.install_from_github(URL)

    assert [i.name for i in infos] == ["root-skill"]
    assert (module.skills_dir / "root-skill" / "SKILL.md").exists()
    assert not (module.skills_dir / "root-skill" / ".git").exists()


def test_nested_single_skill_is_found(module, monkeypatch):
    _stub_clone(
        monkeypatch,
        {"README.md": "# repo", "my-skill/SKILL.md": _skill_md("my-skill"), "my-skill/run.sh": "echo"},
    )

    infos = module.install_from_github(URL)

    assert [i.name for i in infos] == ["my-skill"]
    assert (module.skills_dir / "my-skill" / "run.sh").exists()


def test_multi_skill_repo_installs_each_skill(module, monkeypatch):
    _stub_clone(
        monkeypatch,
        {
            "README.md": "# plugin",
            "skills/alpha/SKILL.md": _skill_md("alpha"),
            "skills/beta/SKILL.md": _skill_md("beta"),
            "skills/beta/helper.py": "print('hi')",
            "skills/notes.txt": "not a skill",
        },
    )

    infos = module.install_from_github(URL)

    assert [i.name for i in infos] == ["alpha", "beta"]
    assert (module.skills_dir / "beta" / "helper.py").exists()
    assert all(i.source_url == URL for i in infos)
    assert (module.skills_dir / "alpha" / ".skill_meta.json").exists()


def test_top_level_multi_skill_dirs_are_all_installed(module, monkeypatch):
    _stub_clone(
        monkeypatch,
        {"one/SKILL.md": _skill_md("one"), "two/SKILL.md": _skill_md("two"), ".github/x": "ci"},
    )

    assert [i.name for i in module.install_from_github(URL)] == ["one", "two"]


def test_repo_without_skill_md_is_rejected_with_expected_layouts(module, monkeypatch):
    _stub_clone(monkeypatch, {"README.md": "# nothing", "src/main.py": "pass"})

    with pytest.raises(ValueError) as exc_info:
        module.install_from_github(URL)
    msg = str(exc_info.value)
    assert "SKILL.md" in msg
    assert URL in msg
    assert "skills/<name>/SKILL.md" in msg
    assert list(module.skills_dir.iterdir()) == []


def test_find_skill_roots_is_shared_by_zip_and_github(module, tmp_path):
    staged = tmp_path / "staged"
    (staged / "skills" / "b").mkdir(parents=True)
    (staged / "skills" / "a").mkdir(parents=True)
    (staged / "skills" / "a" / "SKILL.md").write_text(_skill_md("a"))
    (staged / "skills" / "b" / "SKILL.md").write_text(_skill_md("b"))

    roots = module.find_skill_roots(staged)
    assert [r.name for r in roots] == ["a", "b"]
    # The zip path keeps its single-root contract: first root, name-sorted.
    assert module._find_skill_root(staged) == roots[0]
    assert module.find_skill_roots(tmp_path / "empty-nope") == []


def test_more_than_max_skills_per_repo_is_rejected_before_installing(module, monkeypatch):
    cap = SkillModule.MAX_SKILLS_PER_REPO
    layout = {f"skills/s{i:03d}/SKILL.md": _skill_md(f"s{i:03d}") for i in range(cap + 1)}
    _stub_clone(monkeypatch, layout)

    with pytest.raises(ValueError) as exc_info:
        module.install_from_github(URL)
    assert f"at most {cap}" in str(exc_info.value)
    assert list(module.skills_dir.iterdir()) == []

    # Exactly the cap is fine.
    _stub_clone(monkeypatch, {f"skills/s{i:03d}/SKILL.md": _skill_md(f"s{i:03d}") for i in range(cap)})
    assert len(module.install_from_github(URL)) == cap


# ── clone argument hygiene: branch is user input on the git command line ──


@pytest.mark.parametrize(
    "branch",
    [
        "--upload-pack=touch /tmp/pwn",   # option injection
        "-c",
        "main dev",                        # whitespace
        "main\tx",
        "../../etc",                       # traversal
        "feature/../main",
        "main;rm -rf /",                   # outside the allowed alphabet
        "$(id)",
        "",
    ],
)
def test_invalid_branch_rejected_before_git_runs(module, monkeypatch, tmp_path, branch):
    def _must_not_run(cmd, **kwargs):
        raise AssertionError(f"git must not run for branch {branch!r}: {cmd}")

    monkeypatch.setattr(skill_module_mod.subprocess, "run", _must_not_run)

    with pytest.raises(ValueError):
        module.fetch_github_repo(URL, branch, tmp_path / "clone")


@pytest.mark.parametrize("branch", ["main", "release/v1.2", "feat_x-1.0"])
def test_valid_branch_is_passed_after_double_dash(module, monkeypatch, tmp_path, branch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        dest = Path(cmd[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(_skill_md("ok-skill"), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(skill_module_mod.subprocess, "run", fake_run)

    module.fetch_github_repo(URL, branch, tmp_path / "clone")

    cmd = seen[0]
    assert cmd[cmd.index("-b") + 1] == branch
    # Positional args (url, dest) come after "--" so neither can be read as an option.
    assert cmd[-3:] == ["--", URL, str(tmp_path / "clone")]


def test_clone_failure_returns_generic_error_not_git_stderr(module, monkeypatch, tmp_path):
    def failing_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            128, cmd, stderr="fatal: unable to access 'https://10.0.0.9/': SECRET-STDERR"
        )

    monkeypatch.setattr(skill_module_mod.subprocess, "run", failing_run)

    with pytest.raises(ValueError) as exc:
        module.fetch_github_repo(URL, "main", tmp_path / "clone")
    assert "SECRET-STDERR" not in str(exc.value)
    assert "10.0.0.9" not in str(exc.value)
