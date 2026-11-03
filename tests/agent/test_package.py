"""The release unit: the committed `verksted/` folder is what an install copies (docs/architecture.md
step 3). These tests look at the package as committed (`git archive HEAD:verksted`), not at the working
tree, so an uncommitted file cannot make a broken package pass. They do not depend on the Makefile's
`package` target; the zip it builds is the same folder."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tarfile

import pytest

from conftest import PACKAGE, REPO

REQUIRED = [
    "plugin.yaml",
    "__init__.py",
    "dashboard/manifest.json",
    "dashboard/plugin_api.py",
    "desktop/plugin.js",
    "skills/verksted/SKILL.md",
]
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
STAMPED = re.compile(r"^(\d+\.\d+\.\d+)(?:\s|$)")


def _yaml_version(text: str) -> str:
    m = re.search(r"^version:\s*[\"']?([^\s\"'#]+)", text, re.M)
    assert m, "plugin.yaml has no version: line"
    return m.group(1)


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def test_plugin_yaml_and_dashboard_manifest_carry_the_same_version():
    plugin = _yaml_version((PACKAGE / "plugin.yaml").read_text(encoding="utf-8"))
    manifest = json.loads((PACKAGE / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin), f"plugin.yaml version {plugin!r} is not major.minor.patch"
    assert manifest["version"] == plugin, f"dashboard/manifest.json says {manifest['version']}, plugin.yaml {plugin}"
    assert manifest["name"] == "verksted", "the dashboard name must equal the plugin key"


def test_the_newest_stamped_changes_version_is_not_ahead_of_plugin_yaml():
    plugin = _yaml_version((PACKAGE / "plugin.yaml").read_text(encoding="utf-8"))
    stamped = []
    for line in (REPO / "Changes").read_text(encoding="utf-8").splitlines():
        m = STAMPED.match(line)  # release lines start in column 0: "0.1.0  2026-..."; entries are indented
        if m:
            stamped.append(m.group(1))
    if not stamped:
        # Nothing stamped yet: only {{$NEXT}} heads the file. The check is vacuous, and says so.
        assert "{{$NEXT}}" in (REPO / "Changes").read_text(encoding="utf-8")
        return
    newest = max(stamped, key=_vtuple)
    assert _vtuple(newest) <= _vtuple(plugin), f"Changes has {newest}, plugin.yaml only {plugin}: bump the manifests"


@pytest.fixture(scope="module")
def archive() -> tarfile.TarFile:
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout (git archive needs one): package contents NOT checked")
    done = subprocess.run([git, "archive", "--format=tar", "HEAD:verksted"], cwd=REPO, capture_output=True)
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    return tarfile.open(fileobj=io.BytesIO(done.stdout))


def test_the_committed_package_has_every_file_an_install_needs(archive):
    names = {m.name for m in archive.getmembers() if m.isfile()}
    missing = [r for r in REQUIRED if r not in names]
    assert not missing, f"git archive HEAD:verksted lacks {missing}"
    # the dashboard half names its API file, and that file is there
    manifest = json.loads(archive.extractfile("dashboard/manifest.json").read())
    assert manifest["api"] in {n.rsplit("/", 1)[-1] for n in names if n.startswith("dashboard/")}


def test_the_committed_package_has_no_bytecode_links_or_bulk(archive):
    members = archive.getmembers()
    bad_names = [m.name for m in members if m.name.endswith((".pyc", ".pyo")) or "__pycache__" in m.name.split("/")]
    links = [m.name for m in members if m.issym() or m.islnk()]
    odd = [m.name for m in members if not (m.isfile() or m.isdir())]
    stray = [m.name for m in members if m.name.split("/")[0] in {"tests", "docs", ".dev", ".git"}]
    assert not bad_names, f"bytecode in the package: {bad_names}"
    assert not links, f"links in the package (the Desktop reconcile refuses them): {links}"
    assert not odd, f"neither file nor folder: {odd}"
    assert not stray, f"repo folders inside the package: {stray}"
    total = sum(m.size for m in members if m.isfile())
    assert 0 < total < MAX_PACKAGE_BYTES, total
    assert total < 4 * 1024 * 1024, f"the package is {total} bytes; a source plugin should be far below the 64 MB limit"
