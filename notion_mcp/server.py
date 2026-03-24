"""Notion MCP server with full block type support, deep reads, and snapshot/restore."""

import json
import os

from fastmcp import FastMCP

from .client import NotionClient

mcp = FastMCP(
    "notion-full",
    instructions=(
        "Full-featured Notion MCP server. Supports deep page reading with "
        "recursive children, surgical block editing, insert-at-position, "
        "snapshot/restore, and all block types including tables."
    ),
)

_client: NotionClient | None = None


def _get_client() -> NotionClient:
    global _client
    if _client is None:
        token = os.environ.get("NOTION_API_TOKEN", "")
        if not token:
            raise ValueError(
                "NOTION_API_TOKEN environment variable is required"
            )
        _client = NotionClient(token=token)
    return _client


# ── Search & Page Info ───────────────────────────────────────────────


@mcp.tool
def search_pages(query: str, max_results: int = 10) -> str:
    """Search Notion pages by title. Returns page IDs, titles, and URLs."""
    try:
        client = _get_client()
        pages = client.search_pages(query, page_size=max_results)
        results = []
        for p in pages:
            title_parts = p.get("properties", {}).get("title", {}).get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts)
            results.append({
                "id": p["id"],
                "title": title,
                "url": p.get("url", ""),
                "last_edited": p.get("last_edited_time", ""),
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def search(
    query: str,
    filter_type: str = "",
    max_results: int = 10,
) -> str:
    """Search Notion pages and/or databases.

    Args:
        query: Text to search for.
        filter_type: Restrict results to "page", "database", or "" for both.
        max_results: Maximum number of results to return.
    """
    try:
        client = _get_client()
        ft = filter_type if filter_type in ("page", "database") else None
        items = client.search(query, filter_type=ft, page_size=max_results)
        results = []
        for item in items:
            obj_type = item.get("object", "")
            if obj_type == "page":
                title_parts = (
                    item.get("properties", {}).get("title", {}).get("title", [])
                )
                title = "".join(t.get("plain_text", "") for t in title_parts)
            else:
                # database — title is a top-level list of rich text
                title = "".join(
                    t.get("plain_text", "") for t in item.get("title", [])
                )
            results.append({
                "id": item["id"],
                "object": obj_type,
                "title": title,
                "url": item.get("url", ""),
                "last_edited": item.get("last_edited_time", ""),
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def get_page_info(page_id: str) -> str:
    """Get metadata for a Notion page (title, parent, dates, URL)."""
    try:
        client = _get_client()
        page = client.get_page(page_id)
        title_parts = page.get("properties", {}).get("title", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_parts)
        return json.dumps({
            "id": page["id"],
            "title": title,
            "url": page.get("url", ""),
            "parent": page.get("parent", {}),
            "created_time": page.get("created_time", ""),
            "last_edited_time": page.get("last_edited_time", ""),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Read ─────────────────────────────────────────────────────────────


@mcp.tool
def read_page_deep(
    page_id: str, max_depth: int = 5, format: str = "markdown"
) -> str:
    """Read a Notion page with all nested children recursively.

    Args:
        page_id: The Notion page ID.
        max_depth: Maximum nesting depth to fetch (default 5).
        format: Output format - 'markdown' for readable text, 'json' for full block tree.

    Returns full document content including nested blocks like sub-lists,
    toggle contents, and table rows.
    """
    try:
        client = _get_client()
        blocks = client.get_block_children_deep(page_id, max_depth=max_depth)
        if format == "json":
            return json.dumps(blocks, indent=2, ensure_ascii=False)
        return client.blocks_to_markdown(blocks)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def list_block_children(block_id: str, include_text: bool = True) -> str:
    """List direct children of a block or page (shallow, no recursion).

    Returns block IDs, types, and optionally their text content.
    Useful for finding specific block IDs before surgical edits.
    """
    try:
        client = _get_client()
        blocks = client.get_block_children(block_id)
        results = []
        for b in blocks:
            btype = b.get("type", "")
            entry = {
                "id": b["id"],
                "type": btype,
                "has_children": b.get("has_children", False),
            }
            if include_text:
                bdata = b.get(btype, {})
                rich = bdata.get("rich_text", [])
                entry["text"] = "".join(
                    rt.get("plain_text", "") for rt in rich
                )[:200]
            results.append(entry)
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Surgical Edit ────────────────────────────────────────────────────


@mcp.tool
def update_block_text(block_id: str, new_text: str) -> str:
    """Replace all text in a specific block with new plain text.

    Preserves the block type but replaces all rich text content.
    Use find_replace_in_block for targeted text changes that preserve formatting.
    """
    try:
        client = _get_client()
        result = client.update_block_text(block_id, new_text)
        return json.dumps({"status": "ok", "block_id": result["id"]})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def find_replace_in_block(block_id: str, old_text: str, new_text: str) -> str:
    """Find and replace text within a block, preserving all formatting.

    Replaces occurrences of old_text with new_text in each rich text segment
    while keeping bold, italic, links, and other annotations intact.
    """
    try:
        client = _get_client()
        result = client.find_and_replace_text(block_id, old_text, new_text)
        return json.dumps({"status": "ok", "block_id": result["id"]})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def update_block_rich_text(block_id: str, rich_text_json: str) -> str:
    """Update a block's rich text with fully specified formatting.

    rich_text_json should be a JSON array of rich text objects, e.g.:
    [{"type":"text","text":{"content":"Bold text"},"annotations":{"bold":true}}]
    """
    try:
        client = _get_client()
        rich_text = json.loads(rich_text_json)
        result = client.update_block_rich_text(block_id, rich_text)
        return json.dumps({"status": "ok", "block_id": result["id"]})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Insert ───────────────────────────────────────────────────────────


@mcp.tool
def insert_blocks_after(block_id: str, blocks_json: str) -> str:
    """Insert new blocks after a specific block.

    blocks_json should be a JSON array of block objects. Use make_block format:
    [{"type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":"Hello"}}]}}]

    For simple cases, use insert_text_after instead.
    """
    try:
        client = _get_client()
        blocks = json.loads(blocks_json)
        created = client.insert_blocks_after(block_id, blocks)
        return json.dumps({
            "status": "ok",
            "created_count": len(created),
            "block_ids": [b["id"] for b in created],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def insert_text_after(
    block_id: str, text: str, block_type: str = "paragraph"
) -> str:
    """Insert a simple text block after a specific block.

    Args:
        block_id: The block to insert after.
        text: The text content.
        block_type: Block type (paragraph, heading_1, heading_2, heading_3,
                    bulleted_list_item, numbered_list_item, quote, etc.)
    """
    try:
        client = _get_client()
        block = client.make_block(block_type, text)
        created = client.insert_blocks_after(block_id, [block])
        return json.dumps({
            "status": "ok",
            "block_id": created[0]["id"] if created else None,
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def append_blocks_to_page(page_id: str, blocks_json: str) -> str:
    """Append blocks to the end of a page.

    blocks_json should be a JSON array of block objects.
    """
    try:
        client = _get_client()
        blocks = json.loads(blocks_json)
        created = client.append_children(page_id, blocks)
        return json.dumps({
            "status": "ok",
            "created_count": len(created),
            "block_ids": [b["id"] for b in created],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Delete ───────────────────────────────────────────────────────────


@mcp.tool
def delete_block(block_id: str) -> str:
    """Delete a specific block by ID."""
    try:
        client = _get_client()
        client.delete_block(block_id)
        return json.dumps({"status": "ok", "deleted": block_id})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Snapshot / Restore ───────────────────────────────────────────────


@mcp.tool
def snapshot_page(page_id: str, output_path: str = "") -> str:
    """Create a complete snapshot of a page with all nested children.

    Saves to a JSON file if output_path is provided. The snapshot includes
    the full block tree with recursive children, suitable for restore.

    Default path: ~/notion-snapshots/<page_id>_<timestamp>.json
    """
    try:
        client = _get_client()
        if not output_path:
            import time as _time
            snap_dir = os.path.expanduser("~/notion-snapshots")
            os.makedirs(snap_dir, exist_ok=True)
            ts = _time.strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(snap_dir, f"{page_id}_{ts}.json")

        snapshot = client.snapshot_page(page_id, output_path)
        block_count = _count_blocks(snapshot.get("blocks", []))
        return json.dumps({
            "status": "ok",
            "page_id": page_id,
            "block_count": block_count,
            "snapshot_path": output_path,
            "snapshot_time": snapshot.get("snapshot_time", ""),
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def restore_page_from_snapshot(page_id: str, snapshot_path: str) -> str:
    """Restore a page from a previously saved snapshot.

    WARNING: This deletes all current content and replaces it with the snapshot.
    Always take a snapshot of the current state before restoring.

    Args:
        page_id: The page to restore into.
        snapshot_path: Path to the snapshot JSON file.
    """
    try:
        client = _get_client()
        result = client.restore_page_from_snapshot(page_id, snapshot_path)
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def _count_blocks(blocks: list[dict]) -> int:
    count = len(blocks)
    for b in blocks:
        if b.get("children"):
            count += _count_blocks(b["children"])
    return count


# ── Pages ────────────────────────────────────────────────────────────


@mcp.tool
def create_page(
    parent_id: str,
    title: str,
    parent_type: str = "page_id",
    icon_emoji: str = "",
    children_json: str = "",
) -> str:
    """Create a new Notion page under a parent page or database.

    Args:
        parent_id: ID of the parent page or database.
        title: Title for the new page.
        parent_type: "page_id" (default) or "database_id".
        icon_emoji: Optional emoji character to use as the page icon.
        children_json: Optional JSON array of block objects for initial content.
    """
    try:
        client = _get_client()
        children = json.loads(children_json) if children_json else None
        icon = icon_emoji if icon_emoji else None
        result = client.create_page(
            parent_id=parent_id,
            title=title,
            parent_type=parent_type,
            children=children,
            icon=icon,
        )
        title_parts = result.get("properties", {}).get("title", {}).get("title", [])
        created_title = "".join(t.get("plain_text", "") for t in title_parts)
        return json.dumps({
            "status": "ok",
            "id": result["id"],
            "title": created_title,
            "url": result.get("url", ""),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def update_page(
    page_id: str,
    title: str = "",
    icon_emoji: str = "",
    archived: bool = False,
) -> str:
    """Update a page's title, icon, or archive status.

    Only fields with non-default values are sent in the PATCH body.
    To archive a page pass archived=True; to unarchive pass archived=False
    (but note the server only sends archived when a caller explicitly toggles it —
    see the implementation which guards on the sentinel value).

    Args:
        page_id: The page to update.
        title: New title text. Omit or pass "" to leave unchanged.
        icon_emoji: New icon emoji. Omit or pass "" to leave unchanged.
        archived: Pass True to archive the page. Defaults to False (no change sent).
    """
    try:
        client = _get_client()
        properties = None
        if title:
            properties = {
                "title": {
                    "title": [{"type": "text", "text": {"content": title}}]
                }
            }
        icon = icon_emoji if icon_emoji else None
        # Only send archived=True when explicitly requested; False is the default
        # no-op so we omit it to avoid accidentally unarchiving pages.
        archived_value = True if archived else None
        result = client.update_page(
            page_id=page_id,
            properties=properties,
            icon=icon,
            archived=archived_value,
        )
        return json.dumps({"status": "ok", "id": result["id"]}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def move_page(
    page_id: str,
    new_parent_id: str,
    parent_type: str = "page_id",
) -> str:
    """Move a page to a new parent page or database.

    Args:
        page_id: The page to move.
        new_parent_id: ID of the destination parent.
        parent_type: "page_id" (default) or "database_id".
    """
    try:
        client = _get_client()
        result = client.move_page(page_id, new_parent_id, parent_type=parent_type)
        return json.dumps({"status": "ok", "id": result["id"]}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Comments ─────────────────────────────────────────────────────────


@mcp.tool
def create_comment(page_id: str, text: str) -> str:
    """Add a comment to a Notion page."""
    try:
        client = _get_client()
        result = client.create_comment(page_id, text)
        return json.dumps({"status": "ok", "id": result["id"]}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def list_comments(block_id: str) -> str:
    """List all comments on a page or block."""
    try:
        client = _get_client()
        comments = client.list_comments(block_id)
        results = []
        for c in comments:
            rich = c.get("rich_text", [])
            text = "".join(rt.get("plain_text", "") for rt in rich)
            results.append({
                "id": c["id"],
                "text": text,
                "created_time": c.get("created_time", ""),
                "created_by": c.get("created_by", {}),
            })
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Databases ─────────────────────────────────────────────────────────


@mcp.tool
def get_database(database_id: str) -> str:
    """Get metadata and schema for a Notion database."""
    try:
        client = _get_client()
        db = client.get_database(database_id)
        title = "".join(t.get("plain_text", "") for t in db.get("title", []))
        return json.dumps({
            "id": db["id"],
            "title": title,
            "url": db.get("url", ""),
            "properties": {
                k: v.get("type") for k, v in db.get("properties", {}).items()
            },
            "created_time": db.get("created_time", ""),
            "last_edited_time": db.get("last_edited_time", ""),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def query_database(
    database_id: str,
    filter_json: str = "",
    sorts_json: str = "",
    page_size: int = 100,
) -> str:
    """Query a Notion database with optional filters and sorts.

    Args:
        database_id: The database to query.
        filter_json: JSON string for a Notion filter object. Leave empty for no filter.
        sorts_json: JSON string for a Notion sorts array. Leave empty for no sorting.
        page_size: Maximum rows per page fetch (max 100). All pages are returned.
    """
    try:
        client = _get_client()
        filter_obj = json.loads(filter_json) if filter_json else None
        sorts = json.loads(sorts_json) if sorts_json else None
        rows = client.query_database(
            database_id,
            filter=filter_obj,
            sorts=sorts,
            page_size=page_size,
        )
        return json.dumps({"status": "ok", "count": len(rows), "results": rows}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Users ─────────────────────────────────────────────────────────────


@mcp.tool
def get_self() -> str:
    """Get the current authenticated Notion user or bot info."""
    try:
        client = _get_client()
        user = client.get_self()
        return json.dumps(user, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def list_users() -> str:
    """List all users in the workspace."""
    try:
        client = _get_client()
        users = client.list_users()
        results = [
            {
                "id": u["id"],
                "name": u.get("name", ""),
                "type": u.get("type", ""),
                "avatar_url": u.get("avatar_url", ""),
            }
            for u in users
        ]
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Blocks ────────────────────────────────────────────────────────────


@mcp.tool
def get_block(block_id: str) -> str:
    """Get a single block by ID, including its type, content, and metadata."""
    try:
        client = _get_client()
        block = client.get_block(block_id)
        return json.dumps(block, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Entry Point ──────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
