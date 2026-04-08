"""Notion MCP server with full block type support, deep reads, and snapshot/restore."""

import json
import os
from pathlib import Path

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


# ── Media & Files ────────────────────────────────────────────────────


@mcp.tool
def upload_file(file_path: str) -> str:
    """Upload a file to Notion's own storage.

    Returns a ``file_upload_id`` that can be referenced in image, file, pdf,
    or video blocks via ``{"type": "file_upload", "file_upload": {"id": ...}}``.
    For the common case of uploading an image and creating a block in one step,
    use ``upload_image_as_block`` instead.

    Args:
        file_path: Local path to the file (PNG, JPEG, GIF, SVG, PDF, etc.)
                   Files up to 20 MiB use single-part mode.
    """
    try:
        client = _get_client()
        result = client.upload_file(file_path)
        return json.dumps({
            "status": "ok",
            "file_upload_id": result["id"],
            "filename": result.get("filename", ""),
            "content_type": result.get("content_type", ""),
            "upload_status": result.get("status", ""),
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def upload_image_as_block(
    file_path: str,
    after_block_id: str = "",
    parent_id: str = "",
    caption: str = "",
) -> str:
    """Upload an image file to Notion and insert it as a new image block.

    This is the standard replacement for imgur-hosted images. The image is
    stored in Notion's own workspace storage.

    Args:
        file_path: Local path to the image (PNG, JPEG, GIF, SVG, WEBP, etc.)
        after_block_id: If provided, insert immediately after this block (siblings).
        parent_id: If after_block_id is empty, append to this page/block instead.
        caption: Optional caption text for the image.

    Exactly one of after_block_id or parent_id must be provided.
    """
    try:
        if not after_block_id and not parent_id:
            return json.dumps({
                "status": "error",
                "message": "Must provide either after_block_id or parent_id",
            })
        client = _get_client()
        upload = client.upload_file(file_path)
        block = client.make_image_block(
            file_upload_id=upload["id"],
            caption=caption,
        )
        if after_block_id:
            created = client.insert_blocks_after(after_block_id, [block])
        else:
            created = client.append_children(parent_id, [block])
        return json.dumps({
            "status": "ok",
            "block_id": created[0]["id"] if created else None,
            "file_upload_id": upload["id"],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def replace_image_block(
    block_id: str,
    file_path: str,
    caption: str = "",
) -> str:
    """Replace an existing image block's content with a newly uploaded image.

    Preserves the block's position in the parent by deleting the old block
    and inserting the new one after the previous sibling. If the image block
    is the very first child of its parent, the new block is appended to the
    end (Notion does not support prepend-at-position).

    Args:
        block_id: The existing image block to replace.
        file_path: Local path to the new image.
        caption: Optional caption for the new image (omit to drop the old one).
    """
    try:
        client = _get_client()
        old = client.get_block(block_id)
        if old.get("type") != "image":
            return json.dumps({
                "status": "error",
                "message": f"Block {block_id} is not an image block (got {old.get('type')})",
            })

        parent_info = old.get("parent", {})
        parent_id = (
            parent_info.get("block_id")
            or parent_info.get("page_id")
        )
        if not parent_id:
            return json.dumps({
                "status": "error",
                "message": "Cannot determine parent of image block",
            })

        siblings = client.get_block_children(parent_id)
        idx = next(
            (i for i, b in enumerate(siblings) if b["id"] == block_id),
            None,
        )
        if idx is None:
            return json.dumps({
                "status": "error",
                "message": "Image block not found in parent's children",
            })

        upload = client.upload_file(file_path)
        new_block = client.make_image_block(
            file_upload_id=upload["id"],
            caption=caption,
        )

        client.delete_block(block_id)

        if idx == 0:
            # Notion has no prepend-at-position. Append to end with a warning.
            created = client.append_children(parent_id, [new_block])
            warning = "inserted at end of parent (idx=0, prepend not supported)"
        else:
            anchor = siblings[idx - 1]["id"]
            created = client.insert_blocks_after(anchor, [new_block])
            warning = ""

        resp = {
            "status": "ok",
            "old_block_id": block_id,
            "new_block_id": created[0]["id"] if created else None,
            "file_upload_id": upload["id"],
        }
        if warning:
            resp["warning"] = warning
        return json.dumps(resp)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def download_image_block(block_id: str, output_path: str) -> str:
    """Download the image file from an image block to a local path.

    Works for all three image source types: external URL, Notion-hosted file,
    and file_upload references. Notion-hosted URLs are signed and short-lived,
    so this tool re-fetches them at call time.

    Args:
        block_id: The image block to download from.
        output_path: Local path to write the image to.
    """
    try:
        client = _get_client()
        url = client.get_image_url_from_block(block_id)
        if not url:
            return json.dumps({
                "status": "error",
                "message": "No downloadable URL found in image block",
            })
        result = client.download_file_from_url(url, output_path)
        return json.dumps({"status": "ok", **result})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def upload_file_as_block(
    file_path: str,
    after_block_id: str = "",
    parent_id: str = "",
    caption: str = "",
    name: str = "",
) -> str:
    """Upload a file (PDF, doc, any attachment) to Notion and insert as a file block.

    Analogous to upload_image_as_block but for non-image files. Use this for
    PDFs, spreadsheets, archives, etc.

    Args:
        file_path: Local path to the file.
        after_block_id: If provided, insert after this block (siblings).
        parent_id: If after_block_id is empty, append to this page/block.
        caption: Optional caption.
        name: Optional display name override (defaults to the filename).
    """
    try:
        if not after_block_id and not parent_id:
            return json.dumps({
                "status": "error",
                "message": "Must provide either after_block_id or parent_id",
            })
        client = _get_client()
        upload = client.upload_file(file_path)
        display_name = name or Path(file_path).name
        block = client.make_file_block(
            file_upload_id=upload["id"],
            caption=caption,
            name=display_name,
        )
        if after_block_id:
            created = client.insert_blocks_after(after_block_id, [block])
        else:
            created = client.append_children(parent_id, [block])
        return json.dumps({
            "status": "ok",
            "block_id": created[0]["id"] if created else None,
            "file_upload_id": upload["id"],
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ── Tables ───────────────────────────────────────────────────────────


@mcp.tool
def insert_table_row(
    table_id: str,
    cells_json: str,
    after_row_id: str = "",
) -> str:
    """Insert a new row into an existing table.

    The number of cells must match the table's existing width.

    Args:
        table_id: The table block ID.
        cells_json: JSON array. Each element is either:
            - a plain string (single text segment)
            - a list of strings (multi-segment plain text in one cell)
            Example for a 4-column table:
            '["SMB", "$15K", "$0.015", "$80k - $300k"]'
        after_row_id: Optional row ID to insert after. If empty, append to end.
    """
    try:
        client = _get_client()
        cells = json.loads(cells_json)
        if not isinstance(cells, list):
            return json.dumps({
                "status": "error",
                "message": "cells_json must be a JSON array",
            })
        result = client.insert_table_row_after(
            table_id,
            cells,
            after_row_id=after_row_id or None,
        )
        return json.dumps({
            "status": "ok",
            "block_id": result.get("id", ""),
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def update_table_cell(
    row_id: str,
    cell_index: int,
    new_text: str,
) -> str:
    """Replace the text of a single cell in a table row.

    Args:
        row_id: The table_row block ID.
        cell_index: Zero-based index of the cell within the row.
        new_text: New plain-text content for the cell. Overwrites all
                  existing rich text segments in that cell.
    """
    try:
        client = _get_client()
        result = client.update_table_cell(row_id, cell_index, new_text)
        return json.dumps({"status": "ok", "block_id": result.get("id", "")})
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
    properties_json: str = "",
) -> str:
    """Create a new Notion page under a parent page or database.

    Args:
        parent_id: ID of the parent page or database.
        title: Title for the new page.
        parent_type: "page_id" (default) or "database_id".
        icon_emoji: Optional emoji character to use as the page icon.
        children_json: Optional JSON array of block objects for initial content.
        properties_json: Optional JSON object of additional database properties to set.
            Example for setting Status: {"Status": {"status": {"name": "Done"}}}
            Example for setting Date: {"Due date": {"date": {"start": "2026-04-15"}}}
    """
    try:
        client = _get_client()
        children = json.loads(children_json) if children_json else None
        icon = icon_emoji if icon_emoji else None
        extra_properties = json.loads(properties_json) if properties_json else None
        result = client.create_page(
            parent_id=parent_id,
            title=title,
            parent_type=parent_type,
            children=children,
            icon=icon,
            extra_properties=extra_properties,
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
    properties_json: str = "",
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
        properties_json: Optional JSON object of database properties to set.
            Example: {"Status": {"status": {"name": "Done"}}}
            Example: {"Due date": {"date": {"start": "2026-04-15"}}}
            Can be combined with title — both will be applied.
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
        if properties_json:
            extra = json.loads(properties_json)
            if properties:
                properties.update(extra)
            else:
                properties = extra
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
    """NOT SUPPORTED: Notion's public API does not support moving a page.

    The PATCH /pages/{id} endpoint silently ignores parent field changes --
    the call returns HTTP 200 but the page is not actually moved. This tool
    is preserved only to return a clear error message and point callers to
    the working alternatives.

    To move a page, use one of these instead:
        * copy_page_to_parent -- destructive recreate-and-archive (block IDs
          will change, file_upload media degrades to short-lived URLs)
        * Manually drag the page in the Notion sidebar (recommended for
          pages with nested subpages or heavy media content)
    """
    return json.dumps({
        "status": "error",
        "message": (
            "move_page is not supported. Notion's public API silently ignores "
            "parent changes on PATCH /pages/{id}. Use copy_page_to_parent "
            "for a destructive recreate-and-archive alternative, or move the "
            "page manually in the Notion UI."
        ),
    })


@mcp.tool
def restore_page(page_id: str) -> str:
    """Restore a page (and its children) from trash.

    This is the explicit counterpart to delete_block on a page. ``update_page``
    has a safety guard that blocks archived=False; this dedicated tool is
    the supported way to unarchive.

    When called on a page that was archived via a cascade (for example, a
    section container that was deleted along with its subpages), restoring
    the parent container automatically restores the cascaded descendants.

    Args:
        page_id: The page to restore from trash.
    """
    try:
        client = _get_client()
        result = client.restore_page(page_id)
        return json.dumps({
            "status": "ok",
            "id": result["id"],
            "archived": result.get("archived", False),
            "in_trash": result.get("in_trash", False),
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@mcp.tool
def copy_page_to_parent(
    page_id: str,
    new_parent_id: str,
    parent_type: str = "page_id",
    archive_original: bool = True,
) -> str:
    """Recreate a page under a new parent (destructive workaround for move).

    Since Notion's public API does not support changing a page's parent,
    this tool works around the limitation by creating a new page at the
    target location with the same title, icon, and content, then archiving
    the original.

    LIMITATIONS (read carefully):
        * Block IDs of the new page are different from the original. Any
          external references to original block IDs (graphics-spec.md,
          cross-page links, Change Log entries) will point at the archived
          original and need to be updated.
        * file_upload image blocks are copied as external URL references
          using the Notion-signed URL returned at copy time. Those URLs
          expire within an hour. For permanent preservation, re-upload the
          source files via upload_image_as_block after the copy.
        * child_page descendants (nested subpages) are NOT recursively
          copied -- the tool refuses to run if any are present. Copy them
          individually.
        * Comments, page history, permissions, and synced blocks are not
          copied.

    Args:
        page_id: The source page to copy.
        new_parent_id: The target parent page or database ID.
        parent_type: "page_id" (default) or "database_id".
        archive_original: If True (default), archive the source page after
            the copy completes. Set False to keep both copies side-by-side.
    """
    try:
        client = _get_client()
        result = client.copy_page_to_parent(
            page_id=page_id,
            new_parent_id=new_parent_id,
            parent_type=parent_type,
            archive_original=archive_original,
        )
        return json.dumps(result)
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
