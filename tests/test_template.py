import ast
import pathlib
import re
import shutil
import tomllib

import copier
import pytest
import yaml

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".md",
    ".rst",
    ".sh",
    ".lua",
    ".json",
    ".mod",
    ".qrc",
    ".cfg",
    "",
}


def generate(tmp_path, **data):
    """Generate a project from a copy of the template, without running its tasks."""
    template = tmp_path / "template_copy"
    template.mkdir(parents=True)
    shutil.copy(TEMPLATE / "copier.yml", template / "copier.yml")
    shutil.copytree(TEMPLATE / "template", template / "template")
    project = tmp_path / "project"
    data.setdefault("project_name", "Demo Project")
    data.setdefault("package_name", "demo_pkg")
    copier.run_copy(
        str(template), project, data=data, defaults=True, skip_tasks=True, quiet=True
    )
    return project


def makefile_modules(project):
    """The shared makefile modules the generated Makefile includes."""
    text = (project / "Makefile").read_text()
    return re.search(r"^MAKEFILES_MODULES = (.*)$", text, re.MULTILINE).group(1).split()


def files(project):
    return {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}


def check_well_formed(project):
    """Everything was rendered, and what should parse does."""
    for path in project.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\{\{|\{%", text), (
            f"unrendered Jinja in {path.relative_to(project)}"
        )
        if path.suffix == ".py":
            ast.parse(text)
        elif path.suffix in {".yml", ".yaml"}:
            yaml.safe_load(text)
        elif path.suffix == ".toml":
            tomllib.loads(text)


def answers(project):
    return yaml.safe_load((project / ".copier-answers.yml").read_text())


def test_answers_file_is_written_for_updates(tmp_path):
    project = generate(tmp_path)

    saved = answers(project)

    assert saved["_src_path"]
    assert saved["project_name"] == "Demo Project"
    assert saved["package_slug"] == "demo_pkg"


def test_project_name_and_version_are_used(tmp_path):
    project = generate(tmp_path, project_name="My Cool Tool", initial_version="1.2.3")

    pyproject = tomllib.loads((project / "pyproject.toml").read_text())

    assert pyproject["project"]["name"] == "my-cool-tool"
    assert pyproject["project"]["version"] == "1.2.3"
    assert (project / "VERSION").read_text().strip() == "1.2.3"


@pytest.mark.parametrize("package_name", ["my-package", "1abc"])
def test_a_package_name_that_is_not_an_identifier_is_refused(tmp_path, package_name):
    with pytest.raises(ValueError):
        generate(tmp_path, package_name=package_name)


def test_github_workflows_are_optional(tmp_path):
    with_ci = generate(tmp_path / "a")
    without = generate(tmp_path / "b", github_workflow=False)

    assert ".github/workflows/unit_tests.yml" in files(with_ci)
    assert not any(name.startswith(".github") for name in files(without))


def test_dependabot_follows_the_workflows_and_the_develop_branch(tmp_path):
    with_ci = generate(tmp_path / "a", develop_branch="dev")
    without = generate(tmp_path / "b", github_workflow=False)

    config = yaml.safe_load((with_ci / ".github/dependabot.yml").read_text())

    assert {u["package-ecosystem"] for u in config["updates"]} == {
        "uv",
        "github-actions",
    }
    assert {u["target-branch"] for u in config["updates"]} == {"dev"}
    assert ".github/dependabot.yml" not in files(without)


@pytest.mark.parametrize(
    "ide, present", [("nvim", ".nvim.lua"), ("vscode", ".vscode/settings.json")]
)
def test_the_ide_files_follow_the_ide(tmp_path, ide, present):
    project = generate(tmp_path, ide=ide)

    assert present in files(project)
    others = {".nvim.lua", ".vscode/settings.json"} - {present}
    assert not others & files(project)


def test_ide_none_adds_no_editor_files(tmp_path):
    project = generate(tmp_path, ide="none")

    assert not {".nvim.lua", ".vscode/settings.json"} & files(project)


# Maya specific


def test_maya_files_are_generated(tmp_path):
    project = generate(tmp_path)

    check_well_formed(project)
    assert {
        "mayapy_test.sh",
        "templates/demo_pkg.mod",
        "src/plug-ins/demo_pkg_plugin.py",
        ".env",
    } <= files(project)
    assert (project / "mayapy_test.sh").stat().st_mode & 0o111, (
        "mayapy_test.sh must stay executable"
    )
    assert "MAYAPY=" in (project / ".env").read_text()


def test_maya_makefile_includes_the_maya_module(tmp_path):
    project = generate(tmp_path)
    makefile = (project / "Makefile").read_text()

    assert makefile_modules(project) == ["general/python", "dcc/maya"]
    assert "PYTEST_CMD = ./mayapy_test.sh\n" in makefile, (
        "the script runs the tests itself"
    )
    assert "PYTEST_PDB_CMD = ./mayapy_test.sh --pdb\n" in makefile


def test_the_ci_workflow_runs_on_the_maya_runner(tmp_path):
    workflow = (generate(tmp_path) / ".github/workflows/unit_tests.yml").read_text()

    assert "maya-tests.yml" in workflow


def test_no_blender_files(tmp_path):
    assert not {"extension", "scripts"} & {
        name.split("/")[0] for name in files(generate(tmp_path))
    }


def test_qt_resources_are_optional(tmp_path):
    plain = generate(tmp_path / "a")
    qt = generate(tmp_path / "b", use_qt=True)

    check_well_formed(qt)
    assert not any("resources" in name for name in files(plain))
    assert makefile_modules(plain) == ["general/python", "dcc/maya"]
    assert "BUILD_PREREQS" not in (plain / "Makefile").read_text()
    assert {
        "src/demo_pkg/resources/resources.qrc",
        "src/demo_pkg/resources/icons/home.png",
    } <= files(qt)
    assert makefile_modules(qt) == ["general/python", "dcc/maya", "general/qt"]
    assert "BUILD_PREREQS = qrc" in (qt / "Makefile").read_text()


def test_zed_is_not_offered(tmp_path):
    with pytest.raises(ValueError):
        generate(tmp_path, ide="zed")


def test_the_makefile_fetches_the_shared_modules(tmp_path):
    makefile = (generate(tmp_path) / "Makefile").read_text()

    assert "MAKEFILES_REF ?= v1" in makefile
    assert ".make/%.mk:" in makefile
    assert "include $(MAKEFILES_MODULES:%=.make/%.mk)" in makefile
