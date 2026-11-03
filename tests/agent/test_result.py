"""The closing result block (card k10): request text, the strict-but-tolerant parser, the check
of the named paths against the workspace root. The Windows cases run on ntpath with the
``fakefs`` stub — simulated, not exercised on Windows."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from verksted import paths, result

from fakefs import WinFS


def block(*lines: str) -> str:
    return "Done.\n\n<verksted-result>\n" + "\n".join(lines) + "\n</verksted-result>\n"


# -- parser ---------------------------------------------------------------------------------

def test_the_canonical_block_parses():
    parsed = result.parse(block("summary: Wrote the notes.", "outputs:", "- runs/r1/notes.md", "- /abs/dir"))
    assert parsed == {"block": "ok", "detail": None, "summary": "Wrote the notes.",
                      "items": ["runs/r1/notes.md", "/abs/dir"], "truncated": False}


def test_no_block_is_missing_and_nothing_is_guessed():
    parsed = result.parse("I wrote notes.md and README.md.")
    assert parsed["block"] == "missing" and parsed["items"] == [] and parsed["summary"] is None


@pytest.mark.parametrize("text,detail", [
    ("x <verksted-result>\nsummary: a\noutputs:\n- a.md", "no </verksted-result>"),
    (block("Here is my result:", "summary: s", "outputs: none"), "comes before summary"),
    (block("outputs:", "- a"), "no summary: line"),
    (block("summary: s"), "no outputs: line"),
    (block("summary: s", "outputs:", "- a", "summary: again"), "appears twice"),
    (block("summary: s", "outputs:", "- a", "I also changed b"), "is not a '- path' line"),
    (block("summary: s", "outputs: none", "- a"), "followed by paths"),
    (block("summary:", "outputs: none"), "summary: is empty"),
    ("<verksted-result>\n" + "summary: " + "x" * (17 * 1024) + "\noutputs: none\n</verksted-result>", "longer than"),
])
def test_a_malformed_block_is_unparsable(text, detail):
    parsed = result.parse(text)
    assert parsed["block"] == "unparsable" and detail in parsed["detail"] and parsed["items"] == []


def test_the_unfilled_template_is_unparsable():
    # The request text itself contains the template; a child that echoes it reported nothing.
    parsed = result.parse("Sure.\n" + result.request_text("/work"))
    assert parsed["block"] == "unparsable" and "placeholder" in parsed["detail"]


def test_the_last_block_wins_and_emphasis_fences_and_numbering_are_tolerated():
    text = ("Template: <verksted-result>\nsummary: T\noutputs:\n- t\n</verksted-result>\nreal:\n```xml\n"
            "<VERKSTED-RESULT>\n**Summary:** real one\n**Outputs:**\n1. `runs/a b/notiz.md`\n"
            "2) \"C:\\Users\\Jörg\\x.md\"\n• 'q.md'\n</Verksted-Result>\n```")
    parsed = result.parse(text)
    assert parsed["block"] == "ok" and parsed["summary"] == "real one"
    assert parsed["items"] == ["runs/a b/notiz.md", "C:\\Users\\Jörg\\x.md", "q.md"]


def test_crlf_multiline_summary_and_none():
    parsed = result.parse("<verksted-result>\r\nsummary: one\r\ntwo\r\n\r\noutputs: none.\r\n</verksted-result>")
    assert parsed == {"block": "ok", "detail": None, "summary": "one\ntwo", "items": [], "truncated": False}


def test_paths_are_taken_verbatim_without_expansion():
    parsed = result.parse(block("summary: s", "outputs:", "- ~/x", "- $HOME/y", "- *.md"))
    assert parsed["items"] == ["~/x", "$HOME/y", "*.md"]


def test_caps_truncate_instead_of_failing():
    parsed = result.parse(block("summary: " + "s" * 2500, "outputs:", *[f"- f{i}" for i in range(60)]))
    assert parsed["block"] == "ok" and parsed["truncated"] is True
    assert len(parsed["summary"]) == 2000 and len(parsed["items"]) == 50


def test_only_the_tail_of_a_huge_answer_is_read():
    early = block("summary: early", "outputs: none")
    assert result.parse(early + "x" * (300 * 1024))["block"] == "missing"
    assert result.parse("x" * (300 * 1024) + early)["summary"] == "early"


def test_request_text_names_the_root_and_the_grammar():
    text = result.request_text("/work/Ärger ws")
    assert "`/work/Ärger ws`" in text and "<verksted-result>" in text and "outputs: none" in text


# -- verifier, Linux ------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "Ärger ws"
    (root / "runs/r1/10-research").mkdir(parents=True)
    (root / "runs/r1/10-research/notes.md").write_text("notes")
    (tmp_path / "outside.txt").write_text("x")
    (root / "esc").symlink_to(tmp_path / "outside.txt")
    (root / "escdir").symlink_to(tmp_path, target_is_directory=True)
    (root / "inlink").symlink_to(root / "runs/r1/10-research/notes.md")
    os.mkfifo(root / "fifo")
    return root


def test_each_named_path_is_checked_against_the_workspace_root(ws):
    root = str(ws)
    parsed = result.parse(block(
        "summary: s", "outputs:", "- runs/r1/10-research/notes.md", f"- {root}/runs/r1", "- ghost.md",
        "- ../outside.txt", "- esc", "- escdir/outside.txt", "- inlink", "- fifo", "- C:\\Users\\x\\a.md",
        "- `runs/r1/10-research/notes.md`", "- ~/x", "- *.md", "- /etc"))
    checked = result.verify(parsed, root, paths.LOCAL)
    assert checked["block"] == "ok" and checked["root"] == root
    assert [(o["relative"], o["kind"]) for o in checked["outputs"]] == [
        ("runs/r1/10-research/notes.md", "file"), ("runs/r1", "folder")]
    notes = checked["outputs"][0]
    assert notes["path"] == str(ws / "runs/r1/10-research/notes.md") and notes["size"] == 5
    assert isinstance(notes["mtime"], float) and checked["outputs"][1]["size"] is None
    assert {m["path"]: m["reason"] for m in checked["missing"]} == {
        "ghost.md": "not_found", "../outside.txt": "outside_workspace", "esc": "outside_workspace",
        "escdir/outside.txt": "outside_workspace", "fifo": "not_file_or_folder",
        "C:\\Users\\x\\a.md": "bad_path", "~/x": "not_found", "*.md": "not_found", "/etc": "outside_workspace"}
    # "inlink" resolves to notes.md inside the root: the same output, listed once.


def test_a_block_that_is_not_ok_checks_nothing(ws):
    checked = result.check("no block here", str(ws), paths.LOCAL)
    assert checked["block"] == "missing" and checked["outputs"] == [] and checked["missing"] == []


def test_control_characters_and_overlong_paths_are_bad(ws):
    parsed = {"block": "ok", "items": ["a\tb", "x" * 1100], "summary": "s"}
    checked = result.verify(parsed, str(ws), paths.LOCAL)
    assert [m["reason"] for m in checked["missing"]] == ["bad_path", "bad_path"]
    assert len(checked["missing"][1]["path"]) == 1024


def test_the_root_itself_is_a_folder_output(ws):
    checked = result.verify({"block": "ok", "items": ["."], "summary": "s"}, str(ws), paths.LOCAL)
    assert checked["outputs"][0]["relative"] == "." and checked["outputs"][0]["kind"] == "folder"


# -- verifier, Windows rules on ntpath (simulated, not exercised on Windows) ----------------

def _winfs():
    fs = WinFS()
    fs.mkdir("C:\\Work\\Ärger WS\\runs\\r1")
    fs.write("C:\\Work\\Ärger WS\\runs\\r1\\Notes.md", "abc")
    fs.write("C:\\Other\\secret.txt", "x")
    fs.link("C:\\Work\\Ärger WS\\escape", "C:\\Other")  # a junction out of the workspace
    return fs


def test_windows_paths_are_checked_with_ntpath_rules():
    fs = _winfs()
    stat = lambda p: SimpleNamespace(st_size=3, st_mtime=7.0)
    parsed = {"block": "ok", "summary": "s", "items": [
        "runs\\r1\\notes.md", "c:\\work\\ärger ws\\RUNS\\r1", "runs/r1/Notes.md", "escape\\secret.txt",
        "C:runs\\x", "\\runs\\r1", "\\\\server\\share\\x", "D:\\nothing", "..\\..\\Other\\secret.txt"]}
    checked = result.verify(parsed, "C:\\Work\\Ärger WS", fs.backend(), stat=stat)
    assert [(o["path"], o["relative"], o["kind"]) for o in checked["outputs"]] == [
        ("C:\\Work\\Ärger WS\\runs\\r1\\Notes.md", "runs/r1/Notes.md", "file"),
        ("C:\\Work\\Ärger WS\\runs\\r1", "runs/r1", "folder")]
    assert {m["path"]: m["reason"] for m in checked["missing"]} == {
        "escape\\secret.txt": "outside_workspace", "C:runs\\x": "bad_path", "\\runs\\r1": "bad_path",
        "\\\\server\\share\\x": "bad_path", "D:\\nothing": "not_found",
        "..\\..\\Other\\secret.txt": "outside_workspace"}
