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


_PRIVATE_BLOCK_RE = re.compile(
    r'^%%private[ \t]*\r?\n(.*?)\r?\n%%[ \t]*\r?$',
    re.MULTILINE | re.DOTALL
)
_UNCLOSED_PRIVATE_RE = re.compile(
    r'^%%private[ \t]*\r?\n.*\Z',
    re.MULTILINE | re.DOTALL
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


_FRONTMATTER_RE = re.compile(r'\A---\n(.*?)\n---\n', re.DOTALL)
_AI_SCOPE_NONE  = re.compile(r'^ai_scope:\s*none\s*$', re.MULTILINE | re.IGNORECASE)
_SENSITIVE_HIGH = re.compile(r'^sensitive:\s*HIGH', re.MULTILINE)


def redact_if_classified(text: str, filepath: str = "") -> str:
    """
    If frontmatter marks this file as Tier 3 (ai_scope: none or sensitive: HIGH/CLASSIFIED),
    return a redaction notice instead of the content.
    Otherwise strip %%private%% blocks and return safe content.
    """
    m = _FRONTMATTER_RE.match(text)
    if m:
        fm_yaml = m.group(1)
        if _AI_SCOPE_NONE.search(fm_yaml) or _SENSITIVE_HIGH.search(fm_yaml):
            name = filepath.split("/")[-1] if filepath else "this file"
            return f"[REDACTED: {name} is classified (ai_scope: none / sensitive: HIGH). Content not available to AI.]"
    return strip_private_blocks(text)


def is_classified(text: str) -> bool:
    """Frontmatter-only classification check, for filtering search results without a redaction-notice string."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return False
    fm_yaml = m.group(1)
    return bool(_AI_SCOPE_NONE.search(fm_yaml) or _SENSITIVE_HIGH.search(fm_yaml))


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
        if fp:
            try:
                raw = api.get_file_contents(fp)
                if is_classified(raw):
                    continue  # drop entirely — don't even leak the filename-plus-snippet
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

        return [
            TextContent(
                type="text",
                text=redact_if_classified(content, args["filepath"])
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
       api.append_content(args.get("filepath", ""), args["content"])

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
                safe = redact_if_classified(raw, fp)
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

        return [
            TextContent(
                type="text",
                text=redact_if_classified(content) if type == "content" else content
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
                    r["content"] = redact_if_classified(r["content"], r.get("path", ""))

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
            result["content"] = redact_if_classified(result["content"], args["filepath"])
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
