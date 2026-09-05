"""EP-2 read-consent floor tests (2026-09-04, backlog 135).

Run: OBSIDIAN_API_KEY=test uv run --with pytest --with mcp pytest tests/test_ep2_read_consent.py
"""
import os
import sys

os.environ.setdefault("OBSIDIAN_API_KEY", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mcp_obsidian.tools as t  # noqa: E402


def _set_consent(tmp_path, lines):
    cf = tmp_path / ".read-consent"
    cf.write_text("\n".join(lines), encoding="utf-8")
    t._READ_CONSENT_FILE = cf
    return cf


PLAIN_NOTE = "---\ntags: []\n---\nhello body"
T2_NOTE = "---\ntags: []\nsensitivity_tier: 2\n---\nbody"
T20_NOTE = "---\ntags: []\nsensitivity_tier: 20\n---\nbody"
T3_NOTE = "---\ntags: []\nsensitivity_tier: 3\n---\nbody"


def test_journal_denied_without_consent(tmp_path):
    _set_consent(tmp_path, ["# nothing relevant", "20_Journal/other.md"])
    d = t.ep2_read_denial("20_Journal/2026-05-10.md")
    assert d and "EP-2" in d and "20_Journal/2026-05-10.md" in d


def test_journal_allowed_with_consent(tmp_path):
    _set_consent(tmp_path, ["# comment", "", "20_Journal/2026-05-10.md"])
    assert t.ep2_read_denial("20_Journal/2026-05-10.md") is None


def test_case_and_leading_slash(tmp_path):
    _set_consent(tmp_path, ["20_Journal/2026-05-10.md"])
    assert t.ep2_read_denial("/20_journal/2026-05-10.MD".lower()) is None
    assert t.ep2_read_denial("20_JOURNAL/2026-05-10.md") is None
    d = t.ep2_read_denial("20_JOURNAL/not-consented.md")
    assert d is not None


def test_stversions_copy_needs_own_consent(tmp_path):
    _set_consent(tmp_path, ["20_Journal/2026-05-10.md"])
    d = t.ep2_read_denial(".stversions/20_Journal/2026-05-10.md~20260901")
    assert d is not None  # copy is a different path; hook parity


def test_other_evidence_folders_denied(tmp_path):
    _set_consent(tmp_path, [])
    for p in ("10_Life_OS/Reflections/x.md", "50_Personal/Health/y.md",
              "10_Life_OS/People/Someone/z.md"):
        assert t.ep2_read_denial(p) is not None, p


def test_non_guarded_allowed(tmp_path):
    _set_consent(tmp_path, [])
    assert t.ep2_read_denial("30_Knowledge/AI Agents/note.md", PLAIN_NOTE) is None
    assert t.ep2_read_denial("00_System/AGENTS.md", PLAIN_NOTE) is None


def test_t2_property_needs_consent_anywhere(tmp_path):
    _set_consent(tmp_path, [])
    d = t.ep2_read_denial("50_Personal/somefile.md", T2_NOTE)
    assert d and "Tier 2 by property" in d
    _set_consent(tmp_path, ["50_Personal/somefile.md"])
    assert t.ep2_read_denial("50_Personal/somefile.md", T2_NOTE) is None


def test_tier20_not_matched_as_t2(tmp_path):
    _set_consent(tmp_path, [])
    assert t.ep2_read_denial("50_Personal/somefile.md", T20_NOTE) is None


def test_empty_filepath_allowed(tmp_path):
    _set_consent(tmp_path, [])
    assert t.ep2_read_denial("") is None
    assert t.ep2_read_denial(None) is None


def test_missing_consent_file_fails_closed(tmp_path):
    t._READ_CONSENT_FILE = tmp_path / "does-not-exist"
    assert t.ep2_read_denial("20_Journal/x.md") is not None


def test_consented_tier3_still_redacted(tmp_path):
    # composition used at every call site: denial or redact_if_classified(...)
    _set_consent(tmp_path, ["20_Journal/secret.md"])
    denial = t.ep2_read_denial("20_Journal/secret.md", T3_NOTE)
    out = denial or t.redact_if_classified(T3_NOTE, "20_Journal/secret.md")
    assert denial is None and out.startswith("[REDACTED:")


def test_consented_plain_journal_served_stripped(tmp_path):
    _set_consent(tmp_path, ["20_Journal/ok.md"])
    note = "---\ntags: []\n---\nvisible\n%%private\nhidden\n%%\nafter"
    denial = t.ep2_read_denial("20_Journal/ok.md", note)
    out = denial or t.redact_if_classified(note, "20_Journal/ok.md")
    assert denial is None and "visible" in out and "hidden" not in out
