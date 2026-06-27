# MCP server for Obsidian

MCP server to interact with Obsidian via the Local REST API community plugin.

<a href="https://glama.ai/mcp/servers/3wko1bhuek"><img width="380" height="200" src="https://glama.ai/mcp/servers/3wko1bhuek/badge" alt="server for Obsidian MCP server" /></a>

## Components

### Tools

The server implements multiple tools to interact with Obsidian:

- list_files_in_vault: Lists all files and directories in the root directory of your Obsidian vault
- list_files_in_dir: Lists all files and directories in a specific Obsidian directory
- get_file_contents: Return the content of a single file in your vault.
- search: Search for documents matching a specified text query across all files in the vault
- patch_content: Insert content into an existing note relative to a heading, block reference, or frontmatter field.
- append_content: Append content to a new or existing file in the vault.
- delete_file: Delete a file or directory from your vault.

### Example prompts

Its good to first instruct Claude to use Obsidian. Then it will always call the tool.

The use prompts like this:
- Get the contents of the last architecture call note and summarize them
- Search for all files where Azure CosmosDb is mentioned and quickly explain to me the context in which it is mentioned
- Summarize the last meeting notes and put them into a new note 'summary meeting.md'. Add an introduction so that I can send it via email.

## Configuration

### Obsidian REST API Key

There are two ways to configure the environment with the Obsidian REST API Key. 

1. Add to server config (preferred)

```json
{
  "mcp-obsidian": {
    "command": "uvx",
    "args": [
      "mcp-obsidian"
    ],
    "env": {
      "OBSIDIAN_API_KEY": "<your_api_key_here>",
      "OBSIDIAN_HOST": "<your_obsidian_host>",
      "OBSIDIAN_PORT": "<your_obsidian_port>"
    }
  }
}
```
Sometimes Claude has issues detecting the location of uv / uvx. You can use `which uvx` to find and paste the full path in above config in such cases.

2. Create a `.env` file in the working directory with the following required variables:

```
OBSIDIAN_API_KEY=your_api_key_here
OBSIDIAN_HOST=your_obsidian_host
OBSIDIAN_PORT=your_obsidian_port
```

Note:
- You can find the API key in the Obsidian plugin config
- Default port is 27124 if not specified
- Default host is 127.0.0.1 if not specified

## Quickstart

### Install

#### Obsidian REST API

You need the Obsidian REST API community plugin running: https://github.com/coddingtonbear/obsidian-local-rest-api

Install and enable it in the settings and copy the api key.

#### Claude Desktop

On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`

On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

<details>
  <summary>Development/Unpublished Servers Configuration</summary>
  
```json
{
  "mcpServers": {
    "mcp-obsidian": {
      "command": "uv",
      "args": [
        "--directory",
        "<dir_to>/mcp-obsidian",
        "run",
        "mcp-obsidian"
      ],
      "env": {
        "OBSIDIAN_API_KEY": "<your_api_key_here>",
        "OBSIDIAN_HOST": "<your_obsidian_host>",
        "OBSIDIAN_PORT": "<your_obsidian_port>"
      }
    }
  }
}
```
</details>

<details>
  <summary>Published Servers Configuration</summary>
  
```json
{
  "mcpServers": {
    "mcp-obsidian": {
      "command": "uvx",
      "args": [
        "mcp-obsidian"
      ],
      "env": {
        "OBSIDIAN_API_KEY": "<YOUR_OBSIDIAN_API_KEY>",
        "OBSIDIAN_HOST": "<your_obsidian_host>",
        "OBSIDIAN_PORT": "<your_obsidian_port>"
      }
    }
  }
}
```
</details>

## Development

### Building

To prepare the package for distribution:

1. Sync dependencies and update lockfile:
```bash
uv sync
```

### Debugging

Since MCP servers run over stdio, debugging can be challenging. For the best debugging
experience, we strongly recommend using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector).

You can launch the MCP Inspector via [`npm`](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm) with this command:

```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/mcp-obsidian run mcp-obsidian
```

Upon launching, the Inspector will display a URL that you can access in your browser to begin debugging.

You can also watch the server logs with this command:

```bash
tail -n 20 -f ~/Library/Logs/Claude/mcp-server-mcp-obsidian.log
```

---

## Fork additions — privacy layer

This fork (`FelixIsaac/mcp-obsidian`) adds a content privacy layer on top of the upstream server. Two additions to `tools.py`:

### 1. `%%private%%` block stripping

Obsidian's `%%...%%` comment syntax marks content as hidden — it is excluded from Reading View, PDF exports, and Obsidian Publish. This fork extends that contract to MCP context.

`strip_private_blocks()` strips blocks that begin with `%%private` before file content reaches the model. This is intentionally narrower than stripping all `%%...%%` comments (upstream PR [#148](https://github.com/MarkusPfundstein/mcp-obsidian/pull/148) proposes stripping all of them): local/on-device models are permitted to see non-private `%%` annotations; only blocks explicitly marked `%%private` are withheld from frontier AI.

Example note:

```markdown
My meeting notes from today.

%%private
I'm not comfortable sharing this part with an AI assistant.
%%
```

The `%%private ... %%` block never reaches the model.

### 2. Frontmatter-gated redaction

`redact_if_classified()` checks a file's YAML frontmatter before returning any content. If the file is marked as classified, it returns a redaction notice instead of the content — no body text is forwarded.

**Trigger conditions (either field):**

| Frontmatter field | Value | Effect |
|---|---|---|
| `ai_scope` | `none` | File fully redacted |
| `sensitive` | `HIGH` | File fully redacted |

**Example frontmatter:**

```yaml
---
ai_scope: none
sensitive: HIGH
---
```

**Redaction notice returned to model:**

```
[REDACTED: 2026-04-19.md is classified (ai_scope: none / sensitive: HIGH). Content not available to AI.]
```

Both `get_file_contents` and `batch_get_file_contents` run through this gate.

### Privacy contract summary

```
Vault file
  → redact_if_classified()    # Tier 3 gate — returns notice if classified
    → strip_private_blocks()  # strips %%private...%% blocks
      → model context
```

### Upstream relationship

The `%%private%%` stripping and frontmatter-gate are **not** part of upstream `MarkusPfundstein/mcp-obsidian`. They are maintained in this fork. If upstream PR #148 merges (strip all `%%...%%`), this fork will rebase and narrow its own stripping accordingly.
