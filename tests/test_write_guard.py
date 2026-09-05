"""Write-half guard tests (2026-08-13 OpenClaw parity build).

Run: OBSIDIAN_API_KEY=test uv run --with pytest --with mcp pytest tests/test_write_guard.py
"""
import datetime
import os
import sys

os.environ.setdefault("OBSIDIAN_API_KEY", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_obsidian.tools import deny_write  # noqa: E402

TODAY = datetime.date.today().isoformat()
SENTINEL = "<!-- auth: ai -->\nBreadcrumb line."
SENTINEL_AFTER_HR = "\n---\n<!-- auth: ai -->\nBreadcrumb line."


class FakeApi:
    def __init__(self, files=None):
        self.files = files or {}

    def get_file_contents(self, fp):
        if fp in self.files:
            return self.files[fp]
        raise Exception("404")


T3_NOTE = "---\ntags: []\nsensitivity_tier: 3\n---\nbody"
T2_NOTE = "---\ntags: []\nsensitivity_tier: 2\n---\nbody"
T30_NOTE = "---\ntags: []\nsensitivity_tier: 30\n---\nbody"
OPEN_NOTE = "---\ntags: []\nstatus: complete\n---\nbody"


def test_journal_write_deny():
    assert deny_write(f"20_Journal/{TODAY}.md", FakeApi()) is not None


def test_lane1_sentinel_append_allow():
    assert deny_write(f"20_Journal/{TODAY}.md", FakeApi(),
                      content=SENTINEL, is_append=True) is None
    assert deny_write(f"20_Journal/{TODAY}.md", FakeApi(),
                      content=SENTINEL_AFTER_HR, is_append=True) is None


def test_wrong_sentinel_append_deny():
    assert deny_write(f"20_Journal/{TODAY}.md", FakeApi(),
                      content="no sentinel here", is_append=True) is not None


def test_yesterday_journal_sentinel_deny():
    y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    assert deny_write(f"20_Journal/{y}.md", FakeApi(),
                      content=SENTINEL, is_append=True) is not None


def test_tier3_property_write_deny():
    api = FakeApi({"50_Personal/Gear/Note.md": T3_NOTE})
    assert deny_write("50_Personal/Gear/Note.md", api) is not None


def test_tier2_property_write_deny():
    api = FakeApi({"50_Personal/Gear/Note.md": T2_NOTE})
    assert deny_write("50_Personal/Gear/Note.md", api) is not None


def test_tier30_no_false_match_allow():
    api = FakeApi({"50_Personal/Gear/Note.md": T30_NOTE})
    assert deny_write("50_Personal/Gear/Note.md", api) is None


def test_00_system_deny():
    assert deny_write("00_System/Governance Townhall.md", FakeApi()) is not None
    assert deny_write("00_System/Scripts/x.py", FakeApi()) is not None
    assert deny_write("00_System/Templates/T.md", FakeApi()) is not None


def test_agents_md_deny():
    assert deny_write("AGENTS.md", FakeApi()) is not None


def test_openclaw_proposals_append_allow():
    assert deny_write("00_Inbox/OpenClaw — Proposals.md", FakeApi(),
                      content="proposal text", is_append=True) is None


def test_30_knowledge_put_allow():
    api = FakeApi({"30_Knowledge/AI Agents/Note.md": OPEN_NOTE})
    assert deny_write("30_Knowledge/AI Agents/Note.md", api) is None
    assert deny_write("30_Knowledge/New Note.md", api) is None  # new file


def test_delete_in_health_deny():
    assert deny_write("50_Personal/Health/x.md", FakeApi(), is_delete=True) is not None


def test_delete_outside_ref_tier_deny():
    assert deny_write("50_Personal/Gear/x.md", FakeApi(), is_delete=True) is not None
    assert deny_write("30_Knowledge/x.md", FakeApi(), is_delete=True) is None


def test_case_spoof_deny():
    assert deny_write("20_journal/2026-01-01.MD".lower(), FakeApi()) is not None
    assert deny_write("00_SYSTEM/PISP.md", FakeApi()) is not None
    assert deny_write("10_LIFE_OS/People/X/file.md", FakeApi()) is not None


def test_stversions_mapping():
    assert deny_write(".stversions/20_Journal/2026-08-01~20260812-010101.md",
                      FakeApi()) is not None
    assert deny_write(".stversions/20_Journal/2026-08-01.md", FakeApi()) is not None


def test_source_materials_tier3_path_deny():
    assert deny_write("10_Life_OS/People/Le Jing/Source Materials/x.txt",
                      FakeApi()) is not None


def test_move_rules():
    api = FakeApi()
    # ref → ref ok
    assert deny_write("30_Knowledge/a.md", api, destination="40_Projects/a.md") is None
    # ref → guarded destination denied
    assert deny_write("30_Knowledge/a.md", api,
                      destination="20_Journal/a.md") is not None
    # guarded source denied
    assert deny_write("50_Personal/Health/a.md", api,
                      destination="30_Knowledge/a.md") is not None
    # archive destination denied (record tree)
    assert deny_write("30_Knowledge/a.md", api,
                      destination="60_Archive/a.md") is not None


def test_60_archive_deny():
    assert deny_write("60_Archive/50_Personal/x.md", FakeApi()) is not None
