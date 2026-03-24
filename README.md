<!-- mcp-name: io.github.cphoskins/notion-full-mcp -->
# notion-full-mcp

Full-featured Notion MCP server with deep page reading, surgical block editing, snapshot/restore, and all block types including tables.

## Install

```bash
pip install notion-full-mcp
# or
uv pip install notion-full-mcp
```

## Quick Start

```bash
export NOTION_API_TOKEN="your-token-here"
notion-mcp
```

## Claude Code Integration

Add to `~/.claude.json` under `mcpServers`:

```json
{
  "notion-full": {
    "type": "stdio",
    "command": "uvx",
    "args": ["notion-full-mcp"],
    "env": {
      "NOTION_API_TOKEN": "your-token-here"
    }
  }
}
```

Or for local development:

```json
{
  "notion-full": {
    "type": "stdio",
    "command": "uv",
    "args": ["run", "--directory", "/path/to/notion-full-mcp", "notion-mcp"],
    "env": {
      "NOTION_API_TOKEN": "your-token-here"
    }
  }
}
```

## Tools (24)

### Search & Pages

| Tool | Description |
|------|-------------|
| `search_pages` | Search pages by title |
| `search` | Search pages and/or databases |
| `get_page_info` | Get page metadata (title, parent, dates, URL) |
| `create_page` | Create a new page under a parent page or database |
| `update_page` | Update a page's title, icon, or archive status |
| `move_page` | Move a page to a new parent |

### Reading

| Tool | Description |
|------|-------------|
| `read_page_deep` | Read page with all nested children (recursive) |
| `list_block_children` | List direct children of a block or page |
| `get_block` | Get a single block by ID |

### Editing

| Tool | Description |
|------|-------------|
| `update_block_text` | Replace all text in a block |
| `find_replace_in_block` | Find/replace text preserving formatting |
| `update_block_rich_text` | Update with full rich text specification |

### Inserting

| Tool | Description |
|------|-------------|
| `insert_text_after` | Insert a text block after a specific block (supports all block types: paragraph, heading_1/2/3, bulleted_list_item, numbered_list_item, quote, etc.) |
| `insert_blocks_after` | Insert multiple blocks after a specific block |
| `append_blocks_to_page` | Append blocks to the end of a page |

### Deleting

| Tool | Description |
|------|-------------|
| `delete_block` | Delete a block by ID |

### Comments

| Tool | Description |
|------|-------------|
| `create_comment` | Add a comment to a page |
| `list_comments` | List all comments on a page or block |

### Databases

| Tool | Description |
|------|-------------|
| `get_database` | Get database metadata and schema |
| `query_database` | Query a database with filters and sorts |

### Users

| Tool | Description |
|------|-------------|
| `get_self` | Get the current authenticated user/bot |
| `list_users` | List all workspace users |

### Snapshot / Restore

| Tool | Description |
|------|-------------|
| `snapshot_page` | Save complete page structure as JSON |
| `restore_page_from_snapshot` | Restore page from a snapshot (destructive) |

## Notion API Token

1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Create a new integration
3. Copy the token (starts with `ntn_`)
4. Share the pages/databases you want to access with the integration

## License

MIT
