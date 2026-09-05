from collections.abc import Sequence
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)
import json
import os
import re
from . import obsidian


# Opener tolerates leading whitespace, case variants, and Felix's misspellings
# (%%privte, %%Priavte…) — same fail-closed p…v…t shape the read-guard uses: a
# typo'd marker must protect MORE, never less (M4, 2026-08-12). Still requires
# the marker alone on its own line, so inline prose mentions of "%%private%%"
# (docs explaining the syntax) don't match.
_PRIVATE_OPENER = r'[ \t]*%%\s*p+r*[aeiou]*v[aeiou]*t+[aeiou]*s?[ \t]*'
_PRIVATE_BLOCK_RE = re.compile(
    r'^' + _PRIVATE_OPENER + r'\r?\n(.*?)\r?\n[ \t]*%%[ \t]*\r?$',
    re.MULTILINE | re.DOTALL | re.IGNORECASE
)
_UNCLOSED_PRIVATE_RE = re.compile(
    r'^' + _PRIVATE_OPENER + r'\r?\n.*\Z',
    re.MULTILINE | re.DOTALL | re.IGNORECASE
)
_PRIVATE_OPENER_LINE_RE = re.compile(
    r'^' + _PRIVATE_OPENER + r'\r?$', re.MULTILINE | re.IGNORECASE
)


def strip_private_blocks(text: str) -> str:
    """
    Strip well-formed %%private ... %% blocks — opening and closing markers
    each alone on their own line — so the content between them never reaches
    the model.

    Deliberately does NOT match inline/prose mentions of the literal string
    "%%private%%" (e.g. documentation explaining the syntax, or a code span
    like `%%private%%`) — a real block always has a line break between the
    opening marker and its content; an inline mention doesn't. The original
    regex (`%%private\\b.*?%%`) didn't require that line break, so it matched
    "%%private%%" written inline as prose and silently ate it — found live
    against this vault's own MCP-scaffolding doc, which explains the syntax
    in a sentence.

    Fail-safe for malformed input: if an opening "%%private" marker is found
    alone on its own line with no matching closing "%%" marker afterward,
    everything from that marker to the end of the file is stripped rather
    than left exposed — an unclosed block is treated as still-private rather
    than risking a leak from a typo'd closing marker.
    """
    text = _PRIVATE_BLOCK_RE.sub('', text)
    text = _UNCLOSED_PRIVATE_RE.sub('', text)
    return text


# CRLF tolerated end-to-end and closing --- allowed at EOF (M3: REST responses
# keep raw bytes, \A---\n missed ---\r\n and fell open). Trailing YAML comments
# after ai_scope tolerated (M2: PISP's own template writes `ai_scope: none  # …`).
# sensitive: matches HIGH *or* CLASSIFIED, any case, optionally quoted (M1/F12:
# PISP §6 says either spelling triggers; code only matched exact-case HIGH).
_FRONTMATTER_RE = re.compile(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', re.DOTALL)
_AI_SCOPE_NONE  = re.compile(r'''^ai_scope:\s*['"]?none['"]?\s*(?:#.*)?$''', re.MULTILINE | re.IGNORECASE)
_SENSITIVE_HIGH = re.compile(r'''^sensitive:\s*['"]?(?:HIGH|CLASSIFIED)''', re.MULTILINE | re.IGNORECASE)
# Felix's 2026-08-11 ruling: sensitivity_tier: 3 is co-canonical with sensitive: HIGH
# as a manual Tier-3 marker — it MUST trigger the same redaction. (Gate catch: the
# docs claimed this equivalence before the code implemented it.)
_SENSITIVITY_T3 = re.compile(r'''^sensitivity_tier:\s*['"]?3(?!\d)''', re.MULTILINE)
# Tier-3 BY LOCATION (Felix's Townhall row-(a) ruling, 2026-08-12): raw third-party
# exports under any person's Source Materials/ are .txt/.zip that can't carry
# frontmatter, so the property-based markers above can never protect them. Path
# match is case-insensitive (APFS case-spoof precedent, H1) and tolerates a
# leading slash or vault-absolute-ish prefixes by matching anywhere a clean
# boundary starts the segment chain.
_TIER3_PATH_RE = re.compile(r'(?:^|/)10_life_os/people/[^/]+/source materials/', re.IGNORECASE)

# Tier-2-CONFIDENTIAL-minimum BY LOCATION: any file directly under 10_Life_OS/People/
# (PISP §2.1) — third-party data about named individuals who have not consented to AI
# processing. Added 2026-08-30 after a live-tested gap: a People/ root file with no
# explicit Tier-3 frontmatter marker (e.g. Le Jing/README.md, sensitivity_tier: 2 only)
# was served in full over this MCP lane, even though vault-read-guard.py's Read-tool
# lane already treats the whole People/ tree as evidence-tier and denies it outright.
# This closes that parity gap. Checked AFTER is_tier3_path so the more specific
# Source-Materials message still wins for those paths; this is the floor for
# everything else in a person's folder.
_TIER2_PEOPLE_PATH_RE = re.compile(r'(?:^|/)10_life_os/people/', re.IGNORECASE)


def is_tier3_path(filepath: str) -> bool:
    """Hand-only location: People/*/Source Materials/ (PISP §2.1). No consent lane."""
    return bool(filepath) and bool(_TIER3_PATH_RE.search(filepath.replace("\\", "/")))


def is_people_path(filepath: str) -> bool:
    """Tier 2 CONFIDENTIAL minimum by location: 10_Life_OS/People/ (PISP §2.1). Does NOT
    imply Source Materials (that's stricter and checked separately/first)."""
    return bool(filepath) and bool(_TIER2_PEOPLE_PATH_RE.search(filepath.replace("\\", "/")))


# --- EP-2 read-consent floor for the MCP channel (2026-09-04, backlog 135) ---
# Felix's ruling (2026-09-04, "of course it does"): evidence-tier reads require his
# per-window consent on EVERY channel, not only the local Read tool. Discovered by
# the pilot-6 rerun: this server returned all 30 evidence-tier 20_Journal/ files
# the Read hook had denied hours earlier — 3rd instance of the vault-guard bug
# class. This mirrors ~/.claude/hooks/vault-read-guard.py (folder floor, consent
# lane, .stversions mapping, case-fold, Tier-2-by-property). Deliberate
# deviations, each equal-or-stricter: (1) a consented file here is still served
# through redact_if_classified/strip_private_blocks — this channel CAN strip
# spans, the Read hook cannot and must deny whole files; (2) People/ has no
# consent lane on this channel (redact_if_classified already redacts it
# wholesale, stricter than the hook). Fail-closed: unreadable consent file =
# no consent for guarded paths.
from pathlib import Path as _Path

_VAULT_DIR = _Path("/Users/felix/Documents/Obsidian Vault")
_READ_CONSENT_FILE = _VAULT_DIR / "00_System" / ".read-consent"
_EVIDENCE_TIER_PREFIXES = ("20_journal/", "10_life_os/reflections/",
                           "50_personal/health/", "10_life_os/people/")
_SENSITIVITY_T2 = re.compile(r"""^sensitivity_tier:\s*['"]?2(?!\d)""", re.MULTILINE)


def _consent_entries() -> set:
    try:
        if _READ_CONSENT_FILE.exists():
            return {l.strip() for l in
                    _READ_CONSENT_FILE.read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")}
    except Exception:
        pass  # unreadable consent = no consent (fail closed for guarded paths)
    return set()


def _tier_rel(filepath: str) -> str:
    r = (filepath or "").replace("\\", "/").lstrip("/").lower()
    if r.startswith(".stversions/"):
        r = r[len(".stversions/"):]
    return r


def ep2_read_denial(filepath: str, content: str = ""):
    """Return a denial notice string if this read lacks Felix's per-window
    consent, else None. Folder floor: evidence-tier prefixes (PISP §2.1).
    Property floor: sensitivity_tier: 2 anywhere (U-2 parity with the Read
    hook). Tier 3 is handled by redact_if_classified and is NEVER reachable
    via consent."""
    rel = (filepath or "").replace("\\", "/").lstrip("/")
    if not rel:
        return None
    trel = _tier_rel(rel)
    kind = None
    if any(trel.startswith(p) for p in _EVIDENCE_TIER_PREFIXES):
        kind = "evidence-tier (PISP \u00a72.1 folder floor)"
    elif content and rel.lower().endswith(".md"):
        m = _FRONTMATTER_RE.match(content)
        if m and _SENSITIVITY_T2.search(m.group(1)):
            kind = "Tier 2 by property (sensitivity_tier: 2 \u2014 U-2/PISP \u00a74.2)"
    if kind is None:
        return None
    entries = _consent_entries()
    # Case-insensitive match on the RAW rel (APFS case-spoof, H1) — but NOT on the
    # .stversions-mapped trel: a versioned journal copy needs its own consent entry,
    # matching the Read hook (consent is per-path, and a copy is a different path).
    if rel in entries or rel.lower().lstrip("/") in {e.lower().lstrip("/") for e in entries}:
        return None
    return (f"[DENIED: EP-2 read-guard \u2014 '{rel}' is {kind}. Ask Felix in this chat "
            "to approve this exact path. Do NOT write 00_System/.read-consent yourself "
            "(relayed consent — EP-2). Felix grants via a user-run command or a native "
            "harness approval (OpenClaw 👍), not via the model. Per-window; never T3/"
            "%%private. Do not bypass via raw file reads.]")


def redact_if_classified(text: str, filepath: str = "") -> str:
    """
    If frontmatter marks this file as Tier 3 (ai_scope: none or sensitive: HIGH/CLASSIFIED),
    or it sits under a Tier-2/3-by-location folder, return a redaction notice instead of
    the content. Otherwise strip %%private%% blocks and return safe content.
    """
    if is_tier3_path(filepath):
        name = filepath.split("/")[-1]
        return f"[REDACTED: {name} is Tier 3 by location (People/*/Source Materials/, PISP §2.1). Content not available to AI.]"
    if is_people_path(filepath):
        name = filepath.split("/")[-1] if filepath else "this file"
        return f"[REDACTED: {name} is Tier 2 CONFIDENTIAL minimum by location (10_Life_OS/People/, PISP §2.1) — third-party subject data, no consent on file for AI processing. Content not available to AI.]"
    m = _FRONTMATTER_RE.match(text)
    if m:
        fm_yaml = m.group(1)
        if _AI_SCOPE_NONE.search(fm_yaml) or _SENSITIVE_HIGH.search(fm_yaml) or _SENSITIVITY_T3.search(fm_yaml):
            name = filepath.split("/")[-1] if filepath else "this file"
            return f"[REDACTED: {name} is classified (ai_scope: none / sensitive: HIGH / sensitivity_tier: 3). Content not available to AI.]"
    return strip_private_blocks(text)


def is_classified(text: str) -> bool:
    """Frontmatter-only classification check, for filtering search results without a redaction-notice string."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return False
    fm_yaml = m.group(1)
    return bool(_AI_SCOPE_NONE.search(fm_yaml) or _SENSITIVE_HIGH.search(fm_yaml)
                or _SENSITIVITY_T3.search(fm_yaml))


def redact_search_results(results: list, api: "obsidian.Obsidian", filename_key: str = "filename") -> list:
    """
    Applied to search results (simple search, complex/JsonLogic search): drops results whose
    source file is classified (checked via a per-result frontmatter fetch — real cost, real
    correctness tradeoff, documented in the README), and strips %%private%% blocks from any
    context/content snippets on the results that remain.
    """
    safe_results = []
    for r in results:
        fp = r.get(filename_key) if isinstance(r, dict) else None
        if fp and is_tier3_path(fp):
            continue  # Tier 3 by location — drop wholesale, filename included
        if fp and is_people_path(fp):
            continue  # Tier 2 CONFIDENTIAL minimum by location (People/) — drop wholesale, filename included
        if fp and ep2_read_denial(fp):
            continue  # EP-2 (2026-09-04, backlog 135): evidence-tier without per-window consent — drop wholesale
        if fp:
            try:
                raw = api.get_file_contents(fp)
                if is_classified(raw):
                    continue  # drop entirely — don't even leak the filename-plus-snippet
                if ep2_read_denial(fp, raw):
                    continue  # EP-2: Tier-2-by-property without consent (U-2 parity) — drop wholesale
                if _PRIVATE_OPENER_LINE_RE.search(raw):
                    # H2 (2026-08-12): a snippet taken from INSIDE a %%private
                    # block carries no marker, so snippet-level stripping below
                    # can't see it. Any private-bearing file is dropped from
                    # search results wholesale — fail closed.
                    continue
            except Exception:
                pass  # if we can't verify, fall through and still scrub what we can below
        if isinstance(r, dict):
            r = dict(r)
            if "matches" in r and isinstance(r["matches"], list):
                r["matches"] = [
                    {**m, "context": strip_private_blocks(m.get("context", ""))}
                    if isinstance(m, dict) else m
                    for m in r["matches"]
                ]
        safe_results.append(r)
    return safe_results

# --- Write-half enforcement (2026-08-13, OpenClaw parity build) ---
#
# Until now every guard in this file was read/search-side; the write tools were
# unguarded because the only MCP consumer was Felix's own Claude Code session,
# which carries its own PreToolUse hooks. External harnesses (OpenClaw first)
# get "parity minus governance": reference-tier writes and the Lane-1 journal
# breadcrumb append, with everything else denied server-side.
#
# There is deliberately NO consent-file lane here: 00_System/.write-consent
# authorizes Felix-driven sessions where he is present to give the order; an
# external harness relaying "Felix said so" is exactly the relayed-consent
# pattern the governance rejects (Spec EP-2). MCP writes are allowed by rule
# or not at all.

import datetime

_SENSITIVITY_T2 = re.compile(r'''^sensitivity_tier:\s*['"]?2(?!\d)''', re.MULTILINE)

_EVIDENCE_TIER_DIRS = ("20_journal/", "10_life_os/reflections/", "50_personal/health/",
                       "10_life_os/people/")
# System/config/record trees: no MCP writes, full stop. 00_System includes
# Scripts/ and Templates/; governance-doc changes go through the Townhall and a
# Felix-driven session, never an external harness. 60_Archive is a record.
_SYSTEM_DENY_DIRS = ("00_system/", ".obsidian/", ".smart-env/", ".trash/",
                     ".stversions/", "60_archive/")
_REF_TIER_DIRS = ("30_knowledge/", "40_projects/", "00_inbox/")

# Lane-1 sentinel: an HTML comment whose body is exactly `auth: ai` (AGENTS.md /
# Journal Template v2). Appends may open with blank lines and/or a horizontal
# rule before the sentinel, per the "append only below a horizontal rule" rule.
_LANE1_SENTINEL_RE = re.compile(
    r'\A(?:\s*|-{3,}[ \t]*\r?\n)*<!--\s*auth:\s*ai\s*-->', re.IGNORECASE)
_TODAY_JOURNAL_RE = re.compile(r'\A20_journal/(\d{4}-\d{2}-\d{2})\.md\Z')
# Syncthing sidecar copies map back to their origin path for tier purposes
# (.stversions/20_Journal/x~20260812-0101.md is still the journal).
_STVERSIONS_RE = re.compile(r'\A\.stversions/', re.IGNORECASE)
_VERSION_SUFFIX_RE = re.compile(r'~\d{8}-\d{6}(?=\.[^.]+\Z|\Z)')


def _norm_write_path(filepath: str) -> str:
    """Vault-relative, forward-slashed, lowercase, .stversions mapped to origin."""
    p = (filepath or "").replace("\\", "/").lstrip("/")
    p = _STVERSIONS_RE.sub("", p)
    p = _VERSION_SUFFIX_RE.sub("", p)
    return p.lower()


def _target_property_tier(api, filepath: str):
    """Existing target file's tier by property (None if new/unreadable — path
    rules still apply; a file that doesn't exist yet has no property to honor)."""
    try:
        raw = api.get_file_contents(filepath)
    except Exception:
        return None
    m = _FRONTMATTER_RE.match(raw or "")
    if not m:
        return None
    fm = m.group(1)
    if _AI_SCOPE_NONE.search(fm) or _SENSITIVE_HIGH.search(fm) or _SENSITIVITY_T3.search(fm):
        return 3
    if _SENSITIVITY_T2.search(fm):
        return 2
    return None


def deny_write(filepath: str, api=None, *, content: str = None,
               is_append: bool = False, is_delete: bool = False,
               destination: str = None) -> str | None:
    """Return a denial reason for a write/delete/move against `filepath`,
    or None if allowed. Mirrors the Claude Code hook rules server-side."""
    p = _norm_write_path(filepath)
    if not p:
        return "empty path"
    if p == "agents.md" or p.endswith("/agents.md"):
        return "AGENTS.md is governance surface — no MCP writes"
    if is_tier3_path(p):
        return "Tier 3 by location (People/*/Source Materials/, PISP §2.1) — hand-only"
    for d in _SYSTEM_DENY_DIRS:
        if p.startswith(d):
            return f"'{d.rstrip('/')}' is a system/record tree — no MCP writes (governance changes go through the Townhall via a Felix-driven session)"
    # Moves: both ends must be reference-tier; everything below also re-checks
    # the destination so a move can't smuggle content into a guarded tree.
    if destination is not None:
        dst_reason = deny_write(destination, api)
        if dst_reason:
            return f"move destination denied: {dst_reason}"
        if not any(p.startswith(d) for d in _REF_TIER_DIRS):
            return "move/delete allowed only within reference tier (30_Knowledge / 40_Projects / 00_Inbox)"
        return None
    in_evidence = any(p.startswith(d) for d in _EVIDENCE_TIER_DIRS)
    if in_evidence:
        m = _TODAY_JOURNAL_RE.match(p)
        today = datetime.date.today().isoformat()
        if (is_append and not is_delete and m and m.group(1) == today
                and content is not None and _LANE1_SENTINEL_RE.match(content)):
            return None  # Lane 1: sentinel-marked breadcrumb onto today's daily note
        return ("evidence tier is agent-write-forbidden (EP-2); sole MCP lane is a "
                "Lane-1 sentinel append (<!-- auth: ai -->) to TODAY'S 20_Journal note")
    if is_delete and not any(p.startswith(d) for d in _REF_TIER_DIRS):
        return "delete allowed only within reference tier (30_Knowledge / 40_Projects / 00_Inbox)"
    if api is not None:
        tier = _target_property_tier(api, filepath)
        if tier == 3:
            return "target carries Tier-3 property (sensitivity_tier: 3 / sensitive: HIGH / ai_scope: none) — writes incl. frontmatter are Felix-hand-only (PISP §5.1)"
        if tier == 2:
            return "target carries sensitivity_tier: 2 — this lane cannot read it, so it may not blind-write it"
    return None


def _guard_write(filepath: str, api, **kw):
    reason = deny_write(filepath, api, **kw)
    if reason:
        raise RuntimeError(f"WRITE DENIED by vault governance: {reason} [{filepath}]")


def _bootstrap_journal_frontmatter(filepath: str, content: str, api) -> str:
    """Auto-prepend the canonical daily-note frontmatter, but only when this
    is a Lane-1 sentineled append to TODAY's journal note and that note does
    not exist yet.

    WHY (real gap, found 2026-09-03/04): put_content is unconditionally
    denied on evidence tier — correctly, no exception, that boundary stays
    absolute. But that means the save-to-obsidian skill's documented
    "template first via put_content, then append" flow cannot create a new
    day's note at all. A session hit this crossing midnight: it filed forward
    into the next day via append (the only permitted lane), and the result
    was a real 20_Journal note with NO frontmatter — no type/status/tags —
    that no agent can ever fix afterward, since fixing it means prepending to
    an evidence-tier file, which is exactly the write this guard exists to
    forbid. A malformed file was the only reachable outcome.

    Fix: when this append is about to CREATE a new day's note (Lane-1 sentinel
    already verified by deny_write before this is called), fold the standard
    template into the SAME atomic write, so the file is conformant from its
    first byte. put_content stays fully and permanently blocked — this does
    not reopen that path, it only makes the append lane produce a complete
    file instead of a fragment a human has to patch by hand.
    """
    m = _TODAY_JOURNAL_RE.match(_norm_write_path(filepath))
    if not m or m.group(1) != datetime.date.today().isoformat():
        return content  # not a journal path, or a journal path but not today's
    try:
        api.get_file_contents(filepath)
        return content  # already exists — never prepend to real content
    except Exception:
        pass  # does not exist yet — this call is creating it
    date = m.group(1)
    frontmatter = (
        "---\n"
        "tags: []\n"
        f'created: "{date}"\n'
        "status: draft\n"
        "type: journal-reflection\n"
        "---\n"
    )
    return frontmatter + content


api_key = os.getenv("OBSIDIAN_API_KEY", "")
obsidian_host = os.getenv("OBSIDIAN_HOST", "127.0.0.1")

if api_key == "":
    raise ValueError(f"OBSIDIAN_API_KEY environment variable required. Working directory: {os.getcwd()}")

TOOL_LIST_FILES_IN_VAULT = "obsidian_list_files_in_vault"
TOOL_LIST_FILES_IN_DIR = "obsidian_list_files_in_dir"

class ToolHandler():
    def __init__(self, tool_name: str):
        self.name = tool_name

    def get_tool_description(self) -> Tool:
        raise NotImplementedError()

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        raise NotImplementedError()
    
class ListFilesInVaultToolHandler(ToolHandler):
    def __init__(self):
        super().__init__(TOOL_LIST_FILES_IN_VAULT)

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Lists all files and directories in the root directory of your Obsidian vault.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            },
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)

        files = api.list_files_in_vault()

        return [
            TextContent(
                type="text",
                text=json.dumps(files, indent=2)
            )
        ]
    
class ListFilesInDirToolHandler(ToolHandler):
    def __init__(self):
        super().__init__(TOOL_LIST_FILES_IN_DIR)

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Lists all files and directories that exist in a specific Obsidian directory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dirpath": {
                        "type": "string",
                        "description": "Path to list files from (relative to your vault root). Note that empty directories will not be returned."
                    },
                },
                "required": ["dirpath"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:

        if "dirpath" not in args:
            raise RuntimeError("dirpath argument missing in arguments")

        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)

        files = api.list_files_in_dir(args["dirpath"])

        return [
            TextContent(
                type="text",
                text=json.dumps(files, indent=2)
            )
        ]
    
class GetFileContentsToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_get_file_contents")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Return the content of a single file in your vault.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the relevant file (relative to your vault root).",
                        "format": "path"
                    },
                },
                "required": ["filepath"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "filepath" not in args:
            raise RuntimeError("filepath argument missing in arguments")

        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)

        content = api.get_file_contents(args["filepath"])

        denial = ep2_read_denial(args["filepath"], content)  # EP-2, backlog 135
        return [
            TextContent(
                type="text",
                text=denial if denial else redact_if_classified(content, args["filepath"])
            )
        ]
    
class SearchToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_simple_search")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="""Simple search for documents matching a specified text query across all files in the vault. 
            Use this tool when you want to do a simple text search""",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to a simple search for in the vault."
                    },
                    "context_length": {
                        "type": "integer",
                        "description": "How much context to return around the matching string (default: 100)",
                        "default": 100
                    }
                },
                "required": ["query"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "query" not in args:
            raise RuntimeError("query argument missing in arguments")

        context_length = args.get("context_length", 100)
        
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        results = redact_search_results(api.search(args["query"], context_length), api)

        formatted_results = []
        for result in results:
            formatted_matches = []
            for match in result.get('matches', []):
                context = match.get('context', '')
                match_pos = match.get('match', {})
                start = match_pos.get('start', 0)
                end = match_pos.get('end', 0)
                
                formatted_matches.append({
                    'context': context,
                    'match_position': {'start': start, 'end': end}
                })
                
            formatted_results.append({
                'filename': result.get('filename', ''),
                'score': result.get('score', 0),
                'matches': formatted_matches
            })

        return [
            TextContent(
                type="text",
                text=json.dumps(formatted_results, indent=2)
            )
        ]
    
class AppendContentToolHandler(ToolHandler):
   def __init__(self):
       super().__init__("obsidian_append_content")

   def get_tool_description(self):
       return Tool(
           name=self.name,
           description="Append content to a new or existing file in the vault.",
           inputSchema={
               "type": "object",
               "properties": {
                   "filepath": {
                       "type": "string",
                       "description": "Path to the file (relative to vault root)",
                       "format": "path"
                   },
                   "content": {
                       "type": "string",
                       "description": "Content to append to the file"
                   }
               },
               "required": ["filepath", "content"]
           }
       )

   def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
       if "filepath" not in args or "content" not in args:
           raise RuntimeError("filepath and content arguments required")

       api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
       _guard_write(args.get("filepath", ""), api, content=args["content"], is_append=True)
       content = _bootstrap_journal_frontmatter(args.get("filepath", ""), args["content"], api)
       api.append_content(args.get("filepath", ""), content)

       return [
           TextContent(
               type="text",
               text=f"Successfully appended content to {args['filepath']}"
           )
       ]
   
class PatchContentToolHandler(ToolHandler):
   def __init__(self):
       super().__init__("obsidian_patch_content")

   def get_tool_description(self):
       return Tool(
           name=self.name,
           description="Insert content into an existing note relative to a heading, block reference, or frontmatter field.",
           inputSchema={
               "type": "object",
               "properties": {
                   "filepath": {
                       "type": "string",
                       "description": "Path to the file (relative to vault root)",
                       "format": "path"
                   },
                   "operation": {
                       "type": "string",
                       "description": "Operation to perform (append, prepend, or replace)",
                       "enum": ["append", "prepend", "replace"]
                   },
                   "target_type": {
                       "type": "string",
                       "description": "Type of target to patch",
                       "enum": ["heading", "block", "frontmatter"]
                   },
                   "target": {
                       "type": "string", 
                       "description": "Target identifier (heading path, block reference, or frontmatter field)"
                   },
                   "content": {
                       "type": "string",
                       "description": "Content to insert"
                   }
               },
               "required": ["filepath", "operation", "target_type", "target", "content"]
           }
       )

   def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
       if not all(k in args for k in ["filepath", "operation", "target_type", "target", "content"]):
           raise RuntimeError("filepath, operation, target_type, target and content arguments required")

       api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
       _guard_write(args.get("filepath", ""), api)
       api.patch_content(
           args.get("filepath", ""),
           args.get("operation", ""),
           args.get("target_type", ""),
           args.get("target", ""),
           args.get("content", "")
       )

       return [
           TextContent(
               type="text",
               text=f"Successfully patched content in {args['filepath']}"
           )
       ]
       
class PutContentToolHandler(ToolHandler):
   def __init__(self):
       super().__init__("obsidian_put_content")

   def get_tool_description(self):
       return Tool(
           name=self.name,
           description="Create a new file in your vault or update the content of an existing one in your vault.",
           inputSchema={
               "type": "object",
               "properties": {
                   "filepath": {
                       "type": "string",
                       "description": "Path to the relevant file (relative to your vault root)",
                       "format": "path"
                   },
                   "content": {
                       "type": "string",
                       "description": "Content of the file you would like to upload"
                   }
               },
               "required": ["filepath", "content"]
           }
       )

   def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
       if "filepath" not in args or "content" not in args:
           raise RuntimeError("filepath and content arguments required")

       api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
       _guard_write(args.get("filepath", ""), api)
       api.put_content(args.get("filepath", ""), args["content"])

       return [
           TextContent(
               type="text",
               text=f"Successfully uploaded content to {args['filepath']}"
           )
       ]
   

class DeleteFileToolHandler(ToolHandler):
   def __init__(self):
       super().__init__("obsidian_delete_file")

   def get_tool_description(self):
       return Tool(
           name=self.name,
           description="Delete a file or directory from the vault.",
           inputSchema={
               "type": "object",
               "properties": {
                   "filepath": {
                       "type": "string",
                       "description": "Path to the file or directory to delete (relative to vault root)",
                       "format": "path"
                   },
                   "confirm": {
                       "type": "boolean",
                       "description": "Confirmation to delete the file (must be true)",
                       "default": False
                   }
               },
               "required": ["filepath", "confirm"]
           }
       )

   def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
       if "filepath" not in args:
           raise RuntimeError("filepath argument missing in arguments")
       
       if not args.get("confirm", False):
           raise RuntimeError("confirm must be set to true to delete a file")

       api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
       _guard_write(args["filepath"], api, is_delete=True)
       api.delete_file(args["filepath"])

       return [
           TextContent(
               type="text",
               text=f"Successfully deleted {args['filepath']}"
           )
       ]
   
class ComplexSearchToolHandler(ToolHandler):
   def __init__(self):
       super().__init__("obsidian_complex_search")

   def get_tool_description(self):
       return Tool(
           name=self.name,
           description="""Complex search for documents using a JsonLogic query. 
           Supports standard JsonLogic operators plus 'glob' and 'regexp' for pattern matching. Results must be non-falsy.

           Use this tool when you want to do a complex search, e.g. for all documents with certain tags etc.
           ALWAYS follow query syntax in examples.

           Examples
            1. Match all markdown files
            {"glob": ["*.md", {"var": "path"}]}

            2. Match all markdown files with 1221 substring inside them
            {
              "and": [
                { "glob": ["*.md", {"var": "path"}] },
                { "regexp": [".*1221.*", {"var": "content"}] }
              ]
            }

            3. Match all markdown files in Work folder containing name Keaton
            {
              "and": [
                { "glob": ["*.md", {"var": "path"}] },
                { "regexp": [".*Work.*", {"var": "path"}] },
                { "regexp": ["Keaton", {"var": "content"}] }
              ]
            }
           """,
           inputSchema={
               "type": "object",
               "properties": {
                   "query": {
                       "type": "object",
                       "description": "JsonLogic query object. ALWAYS follow query syntax in examples. \
                            Example 1: {\"glob\": [\"*.md\", {\"var\": \"path\"}]} matches all markdown files \
                            Example 2: {\"and\": [{\"glob\": [\"*.md\", {\"var\": \"path\"}]}, {\"regexp\": [\".*1221.*\", {\"var\": \"content\"}]}]} matches all markdown files with 1221 substring inside them \
                            Example 3: {\"and\": [{\"glob\": [\"*.md\", {\"var\": \"path\"}]}, {\"regexp\": [\".*Work.*\", {\"var\": \"path\"}]}, {\"regexp\": [\"Keaton\", {\"var\": \"content\"}]}]} matches all markdown files in Work folder containing name Keaton \
                        "
                   }
               },
               "required": ["query"]
           }
       )

   def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
       if "query" not in args:
           raise RuntimeError("query argument missing in arguments")

       api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
       raw_results = api.search_json(args.get("query", ""))
       # JsonLogic search responses vary by matched-file shape; only known to carry a "filename" key
       results = redact_search_results(raw_results, api) if isinstance(raw_results, list) else raw_results

       return [
           TextContent(
               type="text",
               text=json.dumps(results, indent=2)
           )
       ]

class BatchGetFileContentsToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_batch_get_file_contents")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Return the contents of multiple files in your vault, concatenated with headers.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepaths": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "description": "Path to a file (relative to your vault root)",
                            "format": "path"
                        },
                        "description": "List of file paths to read"
                    },
                },
                "required": ["filepaths"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "filepaths" not in args:
            raise RuntimeError("filepaths argument missing in arguments")

        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)

        # Redact classified files individually before concatenation
        parts = []
        for fp in args["filepaths"]:
            try:
                raw = api.get_file_contents(fp)
                safe = ep2_read_denial(fp, raw) or redact_if_classified(raw, fp)  # EP-2, backlog 135
            except Exception as e:
                safe = f"Error reading file: {e}"
            parts.append(f"# {fp}\n\n{safe}\n\n---\n\n")
        content = "".join(parts)

        return [
            TextContent(
                type="text",
                text=content
            )
        ]

class PeriodicNotesToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_get_periodic_note")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Get current periodic note for the specified period.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "The period type (daily, weekly, monthly, quarterly, yearly)",
                        "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"]
                    },
                    "type": {
                        "type": "string",
                        "description": "The type of data to get ('content' or 'metadata'). 'content' returns just the content in Markdown format. 'metadata' includes note metadata (including paths, tags, etc.) and the content.",
                        "default": "content",
                        "enum": ["content", "metadata"]
                    }
                },
                "required": ["period"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "period" not in args:
            raise RuntimeError("period argument missing in arguments")

        period = args["period"]
        valid_periods = ["daily", "weekly", "monthly", "quarterly", "yearly"]
        if period not in valid_periods:
            raise RuntimeError(f"Invalid period: {period}. Must be one of: {', '.join(valid_periods)}")
        
        type = args["type"] if "type" in args else "content"
        valid_types = ["content", "metadata"]
        if type not in valid_types:
            raise RuntimeError(f"Invalid type: {type}. Must be one of: {', '.join(valid_types)}")

        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        content = api.get_periodic_note(period,type)

        # EP-2 (2026-09-04, backlog 135): periodic notes are journal files — resolve
        # the note's real path so the consent floor can see it. Fail-closed for
        # daily notes with an unresolvable path (they live in 20_Journal/).
        note_path = ""
        try:
            meta_raw = content if type == "metadata" else api.get_periodic_note(period, "metadata")
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
            if isinstance(meta, dict):
                note_path = (meta.get("path") or "").strip()
        except Exception:
            note_path = ""
        if not note_path and period == "daily":
            note_path = "20_Journal/(unresolved daily note)"
        denial = ep2_read_denial(note_path) if note_path else None

        if type == "content":
            text = denial if denial else redact_if_classified(content, note_path)
        elif denial:
            text = denial
        else:
            # metadata responses embed full note content — previously served
            # UNREDACTED (pre-existing tier-guard hole, closed in this pass).
            try:
                meta = json.loads(content) if isinstance(content, str) else content
                if isinstance(meta, dict) and isinstance(meta.get("content"), str):
                    meta["content"] = redact_if_classified(meta["content"], note_path)
                    text = json.dumps(meta, indent=2)
                else:
                    text = content if isinstance(content, str) else json.dumps(meta, indent=2)
            except Exception:
                text = ("[EP-2: could not parse periodic-note metadata to apply redaction - "
                        "refusing to serve it raw. Use obsidian_get_file_contents on the "
                        "resolved path instead.]")

        return [
            TextContent(
                type="text",
                text=text
            )
        ]
        
class RecentPeriodicNotesToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_get_recent_periodic_notes")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Get most recent periodic notes for the specified period type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "The period type (daily, weekly, monthly, quarterly, yearly)",
                        "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"]
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of notes to return (default: 5)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 50
                    },
                    "include_content": {
                        "type": "boolean",
                        "description": "Whether to include note content (default: false)",
                        "default": False
                    }
                },
                "required": ["period"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "period" not in args:
            raise RuntimeError("period argument missing in arguments")

        period = args["period"]
        valid_periods = ["daily", "weekly", "monthly", "quarterly", "yearly"]
        if period not in valid_periods:
            raise RuntimeError(f"Invalid period: {period}. Must be one of: {', '.join(valid_periods)}")

        limit = args.get("limit", 5)
        if not isinstance(limit, int) or limit < 1:
            raise RuntimeError(f"Invalid limit: {limit}. Must be a positive integer")
            
        include_content = args.get("include_content", False)
        if not isinstance(include_content, bool):
            raise RuntimeError(f"Invalid include_content: {include_content}. Must be a boolean")

        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        results = api.get_recent_periodic_notes(period, limit, include_content)

        if include_content and isinstance(results, list):
            for r in results:
                if isinstance(r, dict) and "content" in r:
                    r["content"] = (ep2_read_denial(r.get("path", ""), r["content"])  # EP-2, backlog 135
                                    or redact_if_classified(r["content"], r.get("path", "")))

        return [
            TextContent(
                type="text",
                text=json.dumps(results, indent=2)
            )
        ]
        
class RecentChangesToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_get_recent_changes")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Get recently modified files in the vault.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of files to return (default: 10)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 100
                    },
                    "days": {
                        "type": "integer",
                        "description": "Only include files modified within this many days (default: 90)",
                        "minimum": 1,
                        "default": 90
                    }
                }
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        limit = args.get("limit", 10)
        if not isinstance(limit, int) or limit < 1:
            raise RuntimeError(f"Invalid limit: {limit}. Must be a positive integer")
            
        days = args.get("days", 90)
        if not isinstance(days, int) or days < 1:
            raise RuntimeError(f"Invalid days: {days}. Must be a positive integer")

        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        results = api.get_recent_changes(limit, days)

        return [
            TextContent(
                type="text",
                text=json.dumps(results, indent=2)
            )
        ]


# --- Active file (ported from obsidian-extra) ---

class ActiveFileReadToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_active_file_read")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Read the full content of the file currently open/active in Obsidian.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        content = api.get_active_file()
        return [TextContent(type="text", text=redact_if_classified(content))]


class ActiveFileWriteToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_active_file_write")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Overwrite the entire content of the currently active file.",
            inputSchema={
                "type": "object",
                "properties": {"content": {"type": "string", "description": "New full file content."}},
                "required": ["content"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "content" not in args:
            raise RuntimeError("content argument missing in arguments")
        # Active-file writes can't verify WHICH file is open (could be a journal
        # or Tier-3 note) — fail closed; use the path-addressed tools instead.
        raise RuntimeError("WRITE DENIED by vault governance: active-file overwrite cannot verify its target's tier — use the path-addressed write tools")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.update_active_file(args["content"])
        return [TextContent(type="text", text="Successfully overwrote active file")]


class ActiveFileAppendToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_active_file_append")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Append content to the end of the currently active file.",
            inputSchema={
                "type": "object",
                "properties": {"content": {"type": "string", "description": "Text to append."}},
                "required": ["content"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "content" not in args:
            raise RuntimeError("content argument missing in arguments")
        # Active-file writes can't verify WHICH file is open (could be a journal
        # or Tier-3 note) — fail closed; use the path-addressed tools instead.
        raise RuntimeError("WRITE DENIED by vault governance: active-file append cannot verify its target's tier — use the path-addressed write tools")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.append_active_file(args["content"])
        return [TextContent(type="text", text="Successfully appended to active file")]


class ActiveFilePatchToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_active_file_patch")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Insert content relative to a heading, block reference, or frontmatter field in the active file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["append", "prepend", "replace"]},
                    "target_type": {"type": "string", "enum": ["heading", "block", "frontmatter"]},
                    "target": {"type": "string", "description": "Heading path, block reference, or frontmatter field."},
                    "content": {"type": "string"}
                },
                "required": ["operation", "target_type", "target", "content"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if not all(k in args for k in ["operation", "target_type", "target", "content"]):
            raise RuntimeError("operation, target_type, target and content arguments required")
        # Active-file writes can't verify WHICH file is open (could be a journal
        # or Tier-3 note) — fail closed; use the path-addressed tools instead.
        raise RuntimeError("WRITE DENIED by vault governance: active-file patch cannot verify its target's tier — use the path-addressed write tools")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.patch_active_file(args["operation"], args["target_type"], args["target"], args["content"])
        return [TextContent(type="text", text="Successfully patched active file")]


class ActiveFileDeleteToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_active_file_delete")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Delete the currently active file.",
            inputSchema={
                "type": "object",
                "properties": {"confirm": {"type": "boolean", "default": False}},
                "required": ["confirm"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if not args.get("confirm", False):
            raise RuntimeError("confirm must be set to true to delete the active file")
        # Active-file writes can't verify WHICH file is open (could be a journal
        # or Tier-3 note) — fail closed; use the path-addressed tools instead.
        raise RuntimeError("WRITE DENIED by vault governance: active-file delete cannot verify its target's tier — use the path-addressed write tools")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.delete_active_file()
        return [TextContent(type="text", text="Successfully deleted active file")]


# --- Periodic note writes (ported from obsidian-extra; get_periodic_note above covers reads) ---

class PeriodicNoteWriteToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_periodic_note_write")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Overwrite the content of the current periodic note for the specified period.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"]},
                    "content": {"type": "string"}
                },
                "required": ["period", "content"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "period" not in args or "content" not in args:
            raise RuntimeError("period and content arguments required")
        # Periodic notes are journal-tree (evidence tier); overwrite is never a
        # sanctioned lane — only the sentinel append below is.
        raise RuntimeError("WRITE DENIED by vault governance: periodic-note overwrite targets the journal tree (evidence tier, EP-2); use obsidian_periodic_note_append with a Lane-1 sentinel for today's daily note")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.update_periodic_note(args["period"], args["content"])
        return [TextContent(type="text", text=f"Successfully wrote {args['period']} periodic note")]


class PeriodicNoteAppendToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_periodic_note_append")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Append content to the current periodic note for the specified period.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"]},
                    "content": {"type": "string"}
                },
                "required": ["period", "content"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "period" not in args or "content" not in args:
            raise RuntimeError("period and content arguments required")
        # Evidence tier: only Lane-1 (sentinel breadcrumb onto TODAY's daily note).
        if args["period"] != "daily" or not _LANE1_SENTINEL_RE.match(args["content"]):
            raise RuntimeError("WRITE DENIED by vault governance: periodic-note appends are limited to period='daily' with a Lane-1 sentinel (<!-- auth: ai -->) opening the appended block (EP-2 Lane 1)")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.append_periodic_note(args["period"], args["content"])
        return [TextContent(type="text", text=f"Successfully appended to {args['period']} periodic note")]


class PeriodicNoteDeleteToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_periodic_note_delete")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Delete the current periodic note for the specified period.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": ["daily", "weekly", "monthly", "quarterly", "yearly"]},
                    "confirm": {"type": "boolean", "default": False}
                },
                "required": ["period", "confirm"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "period" not in args:
            raise RuntimeError("period argument missing in arguments")
        if not args.get("confirm", False):
            raise RuntimeError("confirm must be set to true to delete a periodic note")
        raise RuntimeError("WRITE DENIED by vault governance: periodic notes are journal-tree (evidence tier, EP-2) — deletion is Felix-hand-only")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.delete_periodic_note(args["period"])
        return [TextContent(type="text", text=f"Successfully deleted {args['period']} periodic note")]


# --- Commands ---

class ListCommandsToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_list_commands")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="List all commands registered in Obsidian's command palette (including community-plugin commands).",
            inputSchema={"type": "object", "properties": {}, "required": []}
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        return [TextContent(type="text", text=json.dumps(api.list_commands(), indent=2))]


class ExecuteCommandToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_execute_command")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Execute an Obsidian command by id, as if triggered from the command palette. Triggers the UI action; does not return data from it.",
            inputSchema={
                "type": "object",
                "properties": {"command_id": {"type": "string", "description": "Command id, from obsidian_list_commands."}},
                "required": ["command_id"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "command_id" not in args:
            raise RuntimeError("command_id argument missing in arguments")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.execute_command(args["command_id"])
        return [TextContent(type="text", text=f"Successfully executed command {args['command_id']}")]


# --- Tags ---

class ListTagsToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_list_tags")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="List all tags used across the vault, with usage counts.",
            inputSchema={"type": "object", "properties": {}, "required": []}
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        return [TextContent(type="text", text=json.dumps(api.list_tags(), indent=2))]


# --- Move / rename ---

class MoveFileToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_move_file")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Move or rename a file within the vault.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Current vault-relative path.", "format": "path"},
                    "destination": {"type": "string", "description": "New vault-relative path."}
                },
                "required": ["filepath", "destination"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "filepath" not in args or "destination" not in args:
            raise RuntimeError("filepath and destination arguments required")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        _guard_write(args["filepath"], api, destination=args["destination"])
        api.move_file(args["filepath"], args["destination"])
        return [TextContent(type="text", text=f"Successfully moved {args['filepath']} to {args['destination']}")]


# --- Rich metadata reads ---

class GetFileContentsRichToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_get_file_contents_rich")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Read a vault file with rich metadata. format='note' returns parsed note JSON (frontmatter, tags, stat, content); format='document-map' returns the patch-target map (headings/blocks) useful before patching.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "format": "path"},
                    "format": {"type": "string", "enum": ["note", "document-map"], "default": "note"}
                },
                "required": ["filepath"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "filepath" not in args:
            raise RuntimeError("filepath argument missing in arguments")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        fmt = args.get("format", "note")
        result = api.get_file_contents_rich(args["filepath"], fmt)
        # Rich reads return parsed JSON with a top-level "content" field on format="note" —
        # redact that field in place rather than the whole JSON blob.
        if isinstance(result, dict) and "content" in result:
            result["content"] = (ep2_read_denial(args["filepath"], result["content"])  # EP-2, backlog 135
                                 or redact_if_classified(result["content"], args["filepath"]))
        return [TextContent(type="text", text=json.dumps(result, indent=2))]


# --- Open in Obsidian UI ---

class OpenFileToolHandler(ToolHandler):
    def __init__(self):
        super().__init__("obsidian_open_file")

    def get_tool_description(self):
        return Tool(
            name=self.name,
            description="Tell Obsidian to open a specific note in its UI.",
            inputSchema={
                "type": "object",
                "properties": {"filepath": {"type": "string", "format": "path"}},
                "required": ["filepath"]
            }
        )

    def run_tool(self, args: dict) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        if "filepath" not in args:
            raise RuntimeError("filepath argument missing in arguments")
        api = obsidian.Obsidian(api_key=api_key, host=obsidian_host)
        api.open_file(args["filepath"])
        return [TextContent(type="text", text=f"Successfully opened {args['filepath']}")]
