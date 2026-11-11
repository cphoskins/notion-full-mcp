<!-- mcp-name: io.github.cphoskins/notion-full-mcp -->
# notion-full-mcp

Notion MCP server with deep page reading, surgical block editing, safe snapshot/restore, file uploads (Notion-hosted images and PDFs), table row manipulation, page move and copy, and page restore from trash.

## What's new in v0.4.0

- **Restore can no longer lose content.** `restore_page_from_snapshot` writes the snapshot first and removes the old blocks only after that succeeds. A failed restore rolls back and leaves the page as it was. Subpages, inline databases and Notion-hosted files are never deleted.
- **`move_page` works.** It uses Notion's move endpoint, so the page keeps its ID, content, comments and subpages.
- **`copy_page_to_parent` reports what it could not copy.** Uncopyable block types are listed in `skipped`, and the original is archived only when everything was copied.
- **Large and deep content.** Lists nested more than two levels, tables over 100 rows and text over 2,000 characters are split across requests automatically.
- **Mentions and equations survive edits.** Find/replace, restore and copy keep user, page, database and date mentions and inline equations.
- **Better errors and rate-limit handling.** Errors carry Notion's own code and message. Rate-limited requests are retried using `Retry-After`.
- **Database rows have titles.** Search, page info and create return the title whatever the title column is called.
- **More block types.** Equation, bookmark, embed and externally hosted media blocks round-trip through snapshot/restore and copy.
- **Optional file boundary.** Set `NOTION_MCP_FILE_ROOT` to confine uploads, downloads and snapshots to one directory.

## What's new in v0.3.0

- **Page restore from trash.** `restore_page` explicitly unarchives a page by sending `archived=False`. This is the counterpart to `delete_block` on a page and works around the intentional safety guard in `update_page` that blocks `archived=False`. Cascaded child archives are restored automatically when the parent is restored.
- **Destructive page copy across parents.** `copy_page_to_parent` is the workaround for Notion's public API not supporting parent changes. Recreates the page at the target parent with the same title, icon, and content, then archives the original. Handles paragraphs, headings, lists, tables, dividers, code blocks, and image/file blocks. Refuses to run if the source has nested `child_page` descendants (copy those manually).
- **`move_page` stopped pretending.** Notion's `PATCH /pages/{id}` endpoint silently ignores parent changes, so the tool returned an error instead. (v0.4.0 implements a real move.)

## What's new in v0.2.0

- **Notion file uploads.** Upload images and files directly to Notion's own workspace storage instead of relying on an external image host. Works for PNG, JPEG, SVG, GIF, PDF, and any other Notion-supported attachment type.
- **Image block helpers.** `upload_image_as_block` uploads a file and inserts it at a position in one call. `replace_image_block` swaps an existing image's content while preserving its position in the parent. `download_image_block` pulls any image block's file to disk.
- **Table row manipulation.** `insert_table_row` adds a row to an existing table with automatic width validation. `update_table_cell` replaces the text of a single cell. No more rebuild-the-whole-table for small edits.

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

## Tools (33)

### Search & Pages

| Tool | Description |
|------|-------------|
| `search_pages` | Search pages by title |
| `search` | Search pages and/or databases |
| `get_page_info` | Get page metadata (title, parent, dates, URL) |
| `create_page` | Create a new page under a parent page or database |
| `update_page` | Update a page's title, icon, or archive status |
| `move_page` | Move a page under a new parent page, keeping its ID and content |
| `restore_page` | Restore a page (and its cascade-archived children) from trash |
| `copy_page_to_parent` | Recreate a page under a new parent page or database. Archives the original only when every block was copied |

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
| `insert_text_after` | Insert a text block after a specific block (paragraph, heading_1/2/3, bulleted_list_item, numbered_list_item, quote, callout, toggle, to_do, code, divider) |
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
| `restore_page_from_snapshot` | Restore page content from a snapshot. Writes first, rolls back on failure, never deletes subpages or databases |

### Media & Files

| Tool | Description |
|------|-------------|
| `upload_file` | Upload a file to Notion's own workspace storage (returns `file_upload_id`) |
| `upload_image_as_block` | Upload an image and insert it as a new image block in one step (after a block or appended to a page) |
| `replace_image_block` | Replace an existing image block with a newly uploaded file, preserving its position |
| `download_image_block` | Download the image file from an image block to disk — works for external URLs, Notion-hosted files, and `file_upload` references |
| `upload_file_as_block` | Upload any file (PDF, attachment) and insert it as a file block |

### Tables

| Tool | Description |
|------|-------------|
| `insert_table_row` | Insert a new row into an existing table with automatic width validation |
| `update_table_cell` | Replace the text of a single cell in a table row |

## Block support and limits

- **Round-trip (snapshot/restore and copy):** paragraph, headings (including toggleable), bulleted and numbered lists, to-do, quote, callout, toggle, code, divider, table, columns, table of contents, equation, bookmark, embed, and externally hosted image, file, video, PDF and audio blocks.
- **Never recreated:** subpages, inline databases, synced blocks and Notion-hosted files. Restore leaves them in place on the page (reported as `preserved`); copy reports them as `skipped`.
- **Snapshots:** record subpages and inline databases as blocks, without their own content.
- **Queries:** `query_database` returns at most 500 rows and sets `truncated` when more matched.
- **Find/replace:** matches within one formatting run. A phrase that spans a bold or link boundary is reported as not found.
- **API version:** requests use Notion API version `2022-06-28`.

## Configuration

| Variable | Required | Purpose |
|----------|----------|---------|
| `NOTION_API_TOKEN` | Yes | Integration secret |
| `NOTION_MCP_FILE_ROOT` | No | When set, uploads, downloads and snapshot files must be inside this directory |

## Development

```bash
uv run pytest --cov=notion_mcp      # unit tests, no credentials needed
```

A live check runs every tool against a real workspace. It works inside one scratch page under the parent you name and moves it to trash afterwards:

```bash
export NOTION_API_TOKEN="your-token-here"
export NOTION_TEST_PARENT_PAGE="page-id-shared-with-the-integration"
uv run python scripts/live_check.py
```

## Notion API Token

1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Create a new integration
3. Copy the token (starts with `ntn_`)
4. Share the pages/databases you want to access with the integration

## License

MIT
