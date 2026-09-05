"""Journal frontmatter-bootstrap tests (2026-09-04).

Fixes a real gap: put_content is unconditionally denied on evidence tier
(correctly), which means a brand-new day's 20_Journal note created via the
sole permitted lane (Lane-1 sentineled append) previously landed with NO
frontmatter -- a malformed file no agent could ever fix afterward, since
fixing it means prepending to an evidence-tier file. This makes the append
that CREATES the file also carry the standard template, in one atomic write.

Run: OBSIDIAN_API_KEY=test uv run --with pytest --with mcp pytest tests/test_journal_bootstrap.py
"""
import datetime
import os
import sys

os.environ.setdefault("OBSIDIAN_API_KEY", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mcp_obsidian.tools as t  # noqa: E402

SENTINEL_CONTENT = "<!-- auth: ai -->\n## note\nbody"


class _FakeApiMissing:
    """Simulates a file that does not exist yet."""
    def get_file_contents(self, filepath):
        raise Exception("404 not found")


class _FakeApiExists:
    """Simulates a file that already exists, with real content."""
    def get_file_contents(self, filepath):
        return "---\ntags: []\ncreated: \"2026-01-01\"\nstatus: draft\ntype: journal-reflection\n---\nexisting body"


def test_bootstraps_frontmatter_for_new_todays_note():
    today = datetime.date.today().isoformat()
    path = f"20_Journal/{today}.md"
    out = t._bootstrap_journal_frontmatter(path, SENTINEL_CONTENT, _FakeApiMissing())
    assert out.startswith("---\n")
    assert f'created: "{today}"' in out
    assert "type: journal-reflection" in out
    assert "status: draft" in out
    assert SENTINEL_CONTENT in out


def test_does_not_touch_content_for_existing_note():
    today = datetime.date.today().isoformat()
    path = f"20_Journal/{today}.md"
    out = t._bootstrap_journal_frontmatter(path, SENTINEL_CONTENT, _FakeApiExists())
    # Never prepend to a file that already has real content — even if that
    # content happens to already have frontmatter, this function must not
    # touch it a second time.
    assert out == SENTINEL_CONTENT


def test_does_not_bootstrap_non_journal_paths():
    out = t._bootstrap_journal_frontmatter("30_Knowledge/some-note.md", SENTINEL_CONTENT, _FakeApiMissing())
    assert out == SENTINEL_CONTENT


def test_does_not_bootstrap_prior_day_journal():
    # Only TODAY's note gets bootstrapped -- deny_write already forbids
    # appending to a prior day at all, but this must not silently create a
    # frontmatter-only stub for one either, in case that check ever changes.
    out = t._bootstrap_journal_frontmatter("20_Journal/2020-01-01.md", SENTINEL_CONTENT, _FakeApiMissing())
    assert out == SENTINEL_CONTENT
