"""Notion API client with full block type support, deep reads, and snapshot/restore."""

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
RATE_LIMIT_DELAY = 0.05  # 50ms between requests
BATCH_SIZE = 100  # Notion max children per append


class NotionClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("NOTION_API_TOKEN", "")
        if not self.token:
            raise ValueError("NOTION_API_TOKEN is required")
        self._http = httpx.Client(
            base_url=NOTION_API_BASE,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self):
        self._http.close()

    # ── Low-level API ────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self._http.get(path, params=params)
        r.raise_for_status()
        time.sleep(RATE_LIMIT_DELAY)
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(path, json=body)
        r.raise_for_status()
        time.sleep(RATE_LIMIT_DELAY)
        return r.json()

    def _patch(self, path: str, body: dict) -> dict:
        r = self._http.patch(path, json=body)
        r.raise_for_status()
        time.sleep(RATE_LIMIT_DELAY)
        return r.json()

    def _delete(self, path: str) -> dict:
        r = self._http.delete(path)
        r.raise_for_status()
        time.sleep(RATE_LIMIT_DELAY)
        return r.json()

    # ── Pages ────────────────────────────────────────────────────────

    def get_page(self, page_id: str) -> dict:
        return self._get(f"/pages/{page_id}")

    def search_pages(self, query: str, page_size: int = 10) -> list[dict]:
        body = {
            "query": query,
            "filter": {"property": "object", "value": "page"},
            "page_size": page_size,
        }
        return self._post("/search", body).get("results", [])

    def create_page(
        self,
        parent_id: str,
        title: str,
        parent_type: str = "page_id",
        children: list[dict] | None = None,
        icon: str | None = None,
        cover_url: str | None = None,
        extra_properties: dict | None = None,
    ) -> dict:
        """Create a new page under a parent page or database.

        Args:
            parent_id: ID of the parent page or database.
            title: Page title text.
            parent_type: Either "page_id" or "database_id".
            children: Optional list of block objects to include as initial content.
            icon: Optional emoji character for the page icon.
            cover_url: Optional external URL for the page cover image.
            extra_properties: Optional dict of additional Notion properties to set
                (e.g., Status, Date, Select fields). Merged with the title property.
        """
        properties: dict[str, Any] = {
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        }
        if extra_properties:
            properties.update(extra_properties)
        body: dict[str, Any] = {
            "parent": {parent_type: parent_id},
            "properties": properties,
        }
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        if cover_url:
            body["cover"] = {"type": "external", "external": {"url": cover_url}}
        if children:
            body["children"] = children
        return self._post("/pages", body)

    def update_page(
        self,
        page_id: str,
        properties: dict | None = None,
        icon: str | None = None,
        cover_url: str | None = None,
        archived: bool | None = None,
    ) -> dict:
        """Update a page's properties, icon, cover, or archived status.

        Only fields that are explicitly provided are included in the PATCH body.
        """
        body: dict[str, Any] = {}
        if properties is not None:
            body["properties"] = properties
        if icon is not None:
            body["icon"] = {"type": "emoji", "emoji": icon}
        if cover_url is not None:
            body["cover"] = {"type": "external", "external": {"url": cover_url}}
        if archived is not None:
            body["archived"] = archived
        return self._patch(f"/pages/{page_id}", body)

    def move_page(
        self,
        page_id: str,
        new_parent_id: str,
        parent_type: str = "page_id",
    ) -> dict:
        """Move a page to a new parent page or database."""
        return self._patch(
            f"/pages/{page_id}",
            {"parent": {parent_type: new_parent_id}},
        )

    # ── Comments ─────────────────────────────────────────────────────

    def create_comment(self, page_id: str, text: str) -> dict:
        """Add a comment to a Notion page."""
        return self._post(
            "/comments",
            {
                "parent": {"page_id": page_id},
                "rich_text": [{"type": "text", "text": {"content": text}}],
            },
        )

    def list_comments(self, block_id: str) -> list[dict]:
        """List all comments on a block or page (handles pagination)."""
        comments: list[dict] = []
        cursor = None
        while True:
            params: dict[str, Any] = {"block_id": block_id, "page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get("/comments", params)
            comments.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return comments

    # ── Databases ────────────────────────────────────────────────────

    def get_database(self, database_id: str) -> dict:
        """Get metadata and schema for a Notion database."""
        return self._get(f"/databases/{database_id}")

    def query_database(
        self,
        database_id: str,
        filter: dict | None = None,
        sorts: list | None = None,
        page_size: int = 100,
    ) -> list[dict]:
        """Query a database with optional filter and sort criteria.

        Handles pagination automatically, returning all matching rows up to
        a combined ceiling of 500 items to avoid unbounded requests.
        """
        MAX_ITEMS = 500
        results: list[dict] = []
        cursor = None
        while True:
            body: dict[str, Any] = {"page_size": min(page_size, 100)}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            data = self._post(f"/databases/{database_id}/query", body)
            results.extend(data.get("results", []))
            if not data.get("has_more") or len(results) >= MAX_ITEMS:
                break
            cursor = data.get("next_cursor")
        return results

    # ── Users ────────────────────────────────────────────────────────

    def get_self(self) -> dict:
        """Get the current authenticated user or bot."""
        return self._get("/users/me")

    def list_users(self, page_size: int = 100) -> list[dict]:
        """List all users in the workspace (handles pagination)."""
        users: list[dict] = []
        cursor = None
        while True:
            params: dict[str, Any] = {"page_size": min(page_size, 100)}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get("/users", params)
            users.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return users

    # ── Search (enhanced) ────────────────────────────────────────────

    def search(
        self,
        query: str,
        filter_type: str | None = None,
        page_size: int = 10,
    ) -> list[dict]:
        """Search pages and/or databases.

        Args:
            query: Text to search for.
            filter_type: "page", "database", or None (returns both).
            page_size: Maximum number of results to return.
        """
        body: dict[str, Any] = {"query": query, "page_size": page_size}
        if filter_type in ("page", "database"):
            body["filter"] = {"property": "object", "value": filter_type}
        return self._post("/search", body).get("results", [])

    # ── Blocks ───────────────────────────────────────────────────────

    def get_block(self, block_id: str) -> dict:
        return self._get(f"/blocks/{block_id}")

    def get_block_children(self, block_id: str) -> list[dict]:
        """Get all direct children of a block (handles pagination)."""
        blocks = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get(f"/blocks/{block_id}/children", params)
            blocks.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return blocks

    def get_block_children_deep(self, block_id: str, max_depth: int = 10) -> list[dict]:
        """Recursively fetch all blocks including nested children.

        Each block gets a `children` key with its nested child blocks.
        """
        if max_depth <= 0:
            return []
        blocks = self.get_block_children(block_id)
        for block in blocks:
            if block.get("has_children"):
                block["children"] = self.get_block_children_deep(
                    block["id"], max_depth - 1
                )
        return blocks

    def update_block(self, block_id: str, properties: dict) -> dict:
        """Update a block's properties. `properties` should be the block-type payload.

        Example: update_block(id, {"paragraph": {"rich_text": [...]}})
        """
        return self._patch(f"/blocks/{block_id}", properties)

    def delete_block(self, block_id: str) -> dict:
        return self._delete(f"/blocks/{block_id}")

    def append_children(self, parent_id: str, children: list[dict]) -> list[dict]:
        """Append blocks to a parent. Handles batching for >100 blocks."""
        created = []
        for i in range(0, len(children), BATCH_SIZE):
            batch = children[i : i + BATCH_SIZE]
            data = self._patch(
                f"/blocks/{parent_id}/children", {"children": batch}
            )
            created.extend(data.get("results", []))
        return created

    def insert_blocks_after(self, block_id: str, children: list[dict]) -> list[dict]:
        """Insert blocks after a specific block as siblings.

        Finds the parent of the target block and uses the Notion API's
        ``after`` parameter to insert new blocks immediately after it.
        """
        block = self.get_block(block_id)
        parent_id = block.get("parent", {}).get("block_id") or block.get("parent", {}).get("page_id")
        if not parent_id:
            raise ValueError(f"Cannot determine parent of block {block_id}")

        all_created = []
        for i in range(0, len(children), BATCH_SIZE):
            batch = children[i : i + BATCH_SIZE]
            body: dict[str, Any] = {"children": batch}
            # Use the `after` parameter to insert after the target block
            after_id = block_id if i == 0 else all_created[-1]["id"]
            body["after"] = after_id
            data = self._patch(f"/blocks/{parent_id}/children", body)
            all_created.extend(data.get("results", []))
        return all_created

    # ── Rich Text Helpers ────────────────────────────────────────────

    @staticmethod
    def make_rich_text(
        content: str,
        bold: bool = False,
        italic: bool = False,
        strikethrough: bool = False,
        underline: bool = False,
        code: bool = False,
        color: str = "default",
        link: str | None = None,
    ) -> dict:
        """Create a single rich text element."""
        rt: dict[str, Any] = {
            "type": "text",
            "text": {"content": content},
            "annotations": {
                "bold": bold,
                "italic": italic,
                "strikethrough": strikethrough,
                "underline": underline,
                "code": code,
                "color": color,
            },
        }
        if link:
            rt["text"]["link"] = {"url": link}
        return rt

    @staticmethod
    def convert_rich_text_for_write(rich_texts: list[dict]) -> list[dict]:
        """Convert read-format rich_text to write-format, preserving all annotations."""
        result = []
        for rt in rich_texts:
            item: dict[str, Any] = {
                "type": "text",
                "text": {"content": rt.get("plain_text", "")},
            }
            if rt.get("annotations"):
                item["annotations"] = rt["annotations"]
            if rt.get("text", {}).get("link"):
                item["text"]["link"] = rt["text"]["link"]
            result.append(item)
        return result

    # ── Block Conversion (read → write) ──────────────────────────────

    RICH_TEXT_BLOCK_TYPES = {
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item", "quote",
        "callout", "toggle", "to_do",
    }

    @classmethod
    def convert_block_for_write(cls, block: dict) -> dict | None:
        """Convert a read-format block to write-format for append_children.

        Handles all common block types. Returns None for unsupported types.
        """
        btype = block.get("type", "")
        bdata = block.get(btype, {})

        if btype in cls.RICH_TEXT_BLOCK_TYPES:
            result: dict[str, Any] = {
                "object": "block",
                "type": btype,
                btype: {
                    "rich_text": cls.convert_rich_text_for_write(
                        bdata.get("rich_text", [])
                    ),
                },
            }
            if "color" in bdata:
                result[btype]["color"] = bdata["color"]
            if btype == "to_do" and "checked" in bdata:
                result[btype]["checked"] = bdata["checked"]
            if btype == "callout" and "icon" in bdata:
                result[btype]["icon"] = bdata["icon"]

            # Handle children (for toggles, list items with sub-items, etc.)
            if block.get("children"):
                child_blocks = []
                for child in block["children"]:
                    converted = cls.convert_block_for_write(child)
                    if converted:
                        child_blocks.append(converted)
                if child_blocks:
                    result[btype]["children"] = child_blocks

            return result

        elif btype == "divider":
            return {"object": "block", "type": "divider", "divider": {}}

        elif btype == "table_of_contents":
            return {
                "object": "block",
                "type": "table_of_contents",
                "table_of_contents": {"color": bdata.get("color", "default")},
            }

        elif btype == "code":
            return {
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": cls.convert_rich_text_for_write(
                        bdata.get("rich_text", [])
                    ),
                    "language": bdata.get("language", "plain text"),
                },
            }

        elif btype == "table":
            # Tables require children (rows) inline at creation time
            result = {
                "object": "block",
                "type": "table",
                "table": {
                    "table_width": bdata.get("table_width", 2),
                    "has_column_header": bdata.get("has_column_header", False),
                    "has_row_header": bdata.get("has_row_header", False),
                },
            }
            if block.get("children"):
                row_blocks = []
                for child in block["children"]:
                    converted_row = cls.convert_block_for_write(child)
                    if converted_row:
                        row_blocks.append(converted_row)
                result["table"]["children"] = row_blocks
            return result

        elif btype == "table_row":
            cells = []
            for cell in bdata.get("cells", []):
                cells.append(cls.convert_rich_text_for_write(cell))
            return {
                "object": "block",
                "type": "table_row",
                "table_row": {"cells": cells},
            }

        elif btype == "column_list":
            result = {
                "object": "block",
                "type": "column_list",
                "column_list": {},
            }
            if block.get("children"):
                child_blocks = []
                for child in block["children"]:
                    converted = cls.convert_block_for_write(child)
                    if converted:
                        child_blocks.append(converted)
                if child_blocks:
                    result["column_list"]["children"] = child_blocks
            return result

        elif btype == "column":
            result: dict[str, Any] = {
                "object": "block",
                "type": "column",
                "column": {},
            }
            if block.get("children"):
                child_blocks = []
                for child in block["children"]:
                    converted = cls.convert_block_for_write(child)
                    if converted:
                        child_blocks.append(converted)
                if child_blocks:
                    result["column"]["children"] = child_blocks
            return result

        else:
            # Fallback: if it has rich_text, render as paragraph
            if bdata.get("rich_text"):
                return {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": cls.convert_rich_text_for_write(
                            bdata.get("rich_text", [])
                        ),
                    },
                }
            return None

    # ── Block Creation Helpers ───────────────────────────────────────

    @classmethod
    def make_block(cls, block_type: str, text: str = "", **kwargs) -> dict:
        """Create a write-format block from simple parameters.

        Supports: paragraph, heading_1/2/3, bulleted_list_item,
        numbered_list_item, quote, callout, toggle, to_do, divider, code.
        """
        if block_type == "divider":
            return {"object": "block", "type": "divider", "divider": {}}

        if block_type == "code":
            return {
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [cls.make_rich_text(text)],
                    "language": kwargs.get("language", "plain text"),
                },
            }

        if block_type in cls.RICH_TEXT_BLOCK_TYPES:
            block_data: dict[str, Any] = {
                "rich_text": [cls.make_rich_text(text)],
            }
            if "color" in kwargs:
                block_data["color"] = kwargs["color"]
            if block_type == "to_do":
                block_data["checked"] = kwargs.get("checked", False)
            return {
                "object": "block",
                "type": block_type,
                block_type: block_data,
            }

        raise ValueError(f"Unsupported block type: {block_type}")

    # ── Surgical Text Update ─────────────────────────────────────────

    def update_block_text(self, block_id: str, new_text: str) -> dict:
        """Replace the text content of a block while preserving its type.

        For simple cases where you want to replace all text in a block.
        """
        block = self.get_block(block_id)
        btype = block.get("type", "")
        bdata = block.get(btype, {})

        if btype in self.RICH_TEXT_BLOCK_TYPES:
            return self.update_block(block_id, {
                btype: {
                    "rich_text": [self.make_rich_text(new_text)],
                }
            })
        elif btype == "code":
            return self.update_block(block_id, {
                "code": {
                    "rich_text": [self.make_rich_text(new_text)],
                    "language": bdata.get("language", "plain text"),
                }
            })
        else:
            raise ValueError(f"Cannot update text for block type: {btype}")

    def update_block_rich_text(self, block_id: str, rich_text: list[dict]) -> dict:
        """Replace the rich_text of a block with fully specified rich text elements.

        Use this when you need to preserve formatting (bold, links, etc).
        """
        block = self.get_block(block_id)
        btype = block.get("type", "")

        if btype in self.RICH_TEXT_BLOCK_TYPES:
            return self.update_block(block_id, {btype: {"rich_text": rich_text}})
        elif btype == "code":
            return self.update_block(block_id, {
                "code": {
                    "rich_text": rich_text,
                    "language": block.get("code", {}).get("language", "plain text"),
                }
            })
        else:
            raise ValueError(f"Cannot update rich_text for block type: {btype}")

    def _replace_in_rich_texts(
        self, rich_texts: list[dict], old_text: str, new_text: str
    ) -> list[dict]:
        """Replace old_text with new_text in a list of rich_text segments, preserving formatting."""
        updated = []
        for rt in rich_texts:
            new_rt = {
                "type": "text",
                "text": {
                    "content": rt.get("plain_text", "").replace(old_text, new_text),
                },
            }
            if rt.get("annotations"):
                new_rt["annotations"] = rt["annotations"]
            if rt.get("text", {}).get("link"):
                new_rt["text"]["link"] = rt["text"]["link"]
            updated.append(new_rt)
        return updated

    def find_and_replace_text(
        self, block_id: str, old_text: str, new_text: str
    ) -> dict:
        """Find and replace text within a block's rich_text, preserving formatting.

        Supports all rich_text block types plus table_row cells.
        """
        block = self.get_block(block_id)
        btype = block.get("type", "")
        bdata = block.get(btype, {})

        if btype == "table_row":
            cells = bdata.get("cells", [])
            updated_cells = []
            for cell in cells:
                updated_cells.append(self._replace_in_rich_texts(cell, old_text, new_text))
            return self.update_block(block_id, {
                "table_row": {"cells": updated_cells}
            })

        rich_texts = bdata.get("rich_text", [])
        updated = self._replace_in_rich_texts(rich_texts, old_text, new_text)
        return self.update_block_rich_text(block_id, updated)

    # ── Snapshot / Restore ───────────────────────────────────────────

    def snapshot_page(self, page_id: str, output_path: str | None = None) -> dict:
        """Create a complete snapshot of a page including all nested children.

        Returns the snapshot dict and optionally saves to a JSON file.
        """
        page = self.get_page(page_id)
        blocks = self.get_block_children_deep(page_id)

        snapshot = {
            "page_id": page_id,
            "page": page,
            "blocks": blocks,
            "snapshot_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))

        return snapshot

    def restore_page_from_snapshot(
        self, page_id: str, snapshot: dict | str
    ) -> dict:
        """Restore a page from a snapshot, replacing all current content.

        Args:
            page_id: The page to restore into.
            snapshot: Either a snapshot dict or a file path to a JSON snapshot.

        Returns:
            Summary of the restoration.
        """
        if isinstance(snapshot, str):
            snapshot = json.loads(Path(snapshot).read_text())

        # Step 1: Delete all current blocks
        current = self.get_block_children(page_id)
        deleted = 0
        for block in current:
            try:
                self.delete_block(block["id"])
                deleted += 1
            except httpx.HTTPStatusError:
                pass  # Already deleted or archived

        # Step 2: Recreate blocks from snapshot
        blocks = snapshot.get("blocks", [])
        created = self._restore_blocks(page_id, blocks)

        return {
            "status": "ok",
            "deleted": deleted,
            "created": created,
            "page_id": page_id,
        }

    def _restore_blocks(self, parent_id: str, blocks: list[dict]) -> int:
        """Recursively restore blocks under a parent."""
        count = 0
        write_blocks = []

        for block in blocks:
            converted = self.convert_block_for_write(block)
            if converted:
                write_blocks.append((block, converted))

        # Separate table blocks (need children inline) from others
        batch = []
        for orig, conv in write_blocks:
            batch.append(conv)

        if batch:
            created = self.append_children(parent_id, batch)
            count += len(created)

            # Now handle children for non-table, non-inline-children blocks
            # Tables already have their children inline, but other blocks
            # with children (like toggles, list items) that had children
            # fetched recursively need to be added after creation.
            created_idx = 0
            for orig, conv in write_blocks:
                if created_idx >= len(created):
                    break
                new_id = created[created_idx]["id"]

                # If original had children AND they weren't already inlined
                has_inlined = (
                    orig.get("type") in ("table", "column_list", "column")
                    or conv.get(orig.get("type", ""), {}).get("children")
                )
                if orig.get("children") and not has_inlined:
                    count += self._restore_blocks(new_id, orig["children"])

                created_idx += 1

        return count

    # ── Page-level text extraction (for reading) ─────────────────────

    @staticmethod
    def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> str:
        """Convert a block tree to markdown for human-readable output."""
        lines = []
        indent = "  " * depth
        for block in blocks:
            btype = block.get("type", "")
            bdata = block.get(btype, {})
            rich = bdata.get("rich_text", [])
            text = "".join(rt.get("plain_text", "") for rt in rich)

            if btype == "heading_1":
                lines.append(f"# {text}")
            elif btype == "heading_2":
                lines.append(f"## {text}")
            elif btype == "heading_3":
                lines.append(f"### {text}")
            elif btype == "paragraph":
                lines.append(f"{indent}{text}")
            elif btype == "bulleted_list_item":
                lines.append(f"{indent}- {text}")
            elif btype == "numbered_list_item":
                lines.append(f"{indent}1. {text}")
            elif btype == "to_do":
                check = "x" if bdata.get("checked") else " "
                lines.append(f"{indent}- [{check}] {text}")
            elif btype == "quote":
                lines.append(f"{indent}> {text}")
            elif btype == "callout":
                lines.append(f"{indent}> **Note:** {text}")
            elif btype == "code":
                lang = bdata.get("language", "")
                lines.append(f"{indent}```{lang}\n{indent}{text}\n{indent}```")
            elif btype == "divider":
                lines.append(f"{indent}---")
            elif btype == "toggle":
                lines.append(f"{indent}<toggle> {text}")
            elif btype == "table":
                lines.append(f"{indent}[table]")
            elif btype == "table_row":
                cells = bdata.get("cells", [])
                row = " | ".join(
                    "".join(rt.get("plain_text", "") for rt in cell)
                    for cell in cells
                )
                lines.append(f"{indent}| {row} |")
            else:
                if text:
                    lines.append(f"{indent}{text}")

            if block.get("children"):
                lines.append(
                    NotionClient.blocks_to_markdown(block["children"], depth + 1)
                )

        return "\n".join(lines)
