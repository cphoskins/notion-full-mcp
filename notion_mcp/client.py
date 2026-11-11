"""Notion API client with full block type support, deep reads, and snapshot/restore."""

import json
import mimetypes
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
RATE_LIMIT_DELAY = 0.05  # 50ms between requests
BATCH_SIZE = 100  # Notion max children per append
MAX_NESTING = 2  # Notion max levels of nested children per request
MAX_TEXT_LENGTH = 2000  # Notion max characters per rich text object
MAX_RETRIES = 5  # attempts for rate-limited (429/529) requests
FILE_ROOT_ENV = "NOTION_MCP_FILE_ROOT"


class NotionAPIError(Exception):
    """A Notion API error response, carrying Notion's own code and message."""

    def __init__(self, status: int, code: str, message: str, method: str, path: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(
            f"Notion API error {status} ({code}) on {method} {path}: {message}"
        )


def _raise_for_error(r: httpx.Response, method: str, path: str) -> None:
    if not r.is_error:
        return
    try:
        data = r.json()
    except ValueError:
        data = {}
    raise NotionAPIError(
        r.status_code,
        data.get("code", "unknown_error"),
        data.get("message") or r.text[:500],
        method,
        path,
    )


def page_title(page: dict) -> str:
    """Plain-text title of a page, whatever its title property is called.

    Pages in a database key the title by the column name (e.g. "Name"), so
    the property is found by type, not by the literal key "title".
    """
    for prop in page.get("properties", {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return ""


def resolve_local_path(path: str) -> Path:
    """Expand ``~`` and enforce the optional NOTION_MCP_FILE_ROOT boundary."""
    resolved = Path(path).expanduser().resolve()
    root = os.environ.get(FILE_ROOT_ENV, "")
    if root:
        root_path = Path(root).expanduser().resolve()
        if not resolved.is_relative_to(root_path):
            raise ValueError(
                f"Path {resolved} is outside {FILE_ROOT_ENV} ({root_path})"
            )
    return resolved


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

    def _request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(1, MAX_RETRIES + 1):
            r = self._http.request(method, path, **kwargs)
            if r.status_code in (429, 529) and attempt < MAX_RETRIES:
                try:
                    delay = float(r.headers.get("Retry-After", 1))
                except (TypeError, ValueError):
                    delay = 1.0
                time.sleep(delay * attempt)
                continue
            break
        _raise_for_error(r, method, path)
        time.sleep(RATE_LIMIT_DELAY)
        return r.json()

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json=body)

    def _patch(self, path: str, body: dict) -> dict:
        return self._request("PATCH", path, json=body)

    def _delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    # ── Pages ────────────────────────────────────────────────────────

    def get_page(self, page_id: str) -> dict:
        return self._get(f"/pages/{page_id}")

    def search_pages(self, query: str, page_size: int = 10) -> list[dict]:
        body = {
            "query": query,
            "filter": {"property": "object", "value": "page"},
            "page_size": max(1, min(page_size, 100)),
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
        """Move a page under a new parent page, keeping its ID and content.

        Uses ``POST /pages/{id}/move``. Only page parents are supported;
        moving into a database is not implemented yet.
        """
        if parent_type != "page_id":
            raise ValueError(
                "move_page supports parent_type='page_id' only. To place a "
                "page in a database, use copy_page_to_parent."
            )
        return self._post(
            f"/pages/{page_id}/move",
            {"parent": {"type": "page_id", "page_id": new_parent_id}},
        )

    def restore_page(self, page_id: str) -> dict:
        """Restore a page from trash by sending archived=False.

        This is the explicit counterpart to ``delete_block(page_id)`` which
        archives a page (moves it to trash). ``update_page`` has a safety
        guard that blocks archived=False to prevent accidental restoration,
        so this dedicated method is the supported way to unarchive.

        Cascades automatically restore direct-child pages and blocks that
        were archived as part of the same delete cascade.
        """
        return self._patch(f"/pages/{page_id}", {"archived": False})

    def copy_page_to_parent(
        self,
        page_id: str,
        new_parent_id: str,
        parent_type: str = "page_id",
        archive_original: bool = True,
    ) -> dict:
        """Recreate a page under a new parent (use move_page when possible).

        Creates a new page at ``new_parent_id`` with the same title, emoji
        icon, and content, then optionally archives the original. Useful for
        copying, or for placing a page in a database.

        LIMITATIONS:
            * Block IDs of the new page differ from the original, so links
              to the original blocks go stale.
            * Notion-hosted images and files are copied as ``external``
              references to Notion's signed URLs, which expire in about an
              hour. Re-upload the source files for a permanent copy.
            * Nested subpages are not copied. If the page contains any
              child_page blocks, this method raises.
            * Block types that cannot be recreated are reported in
              ``skipped``. When anything was skipped the original is NOT
              archived, whatever ``archive_original`` says.
            * Comments, page history, permissions, and synced blocks are not
              copied.

        Returns a dict with the new page id, url, archive status, and any
        skipped block types.
        """
        source = self.get_page(page_id)
        title = page_title(source)
        icon = source.get("icon")
        icon_emoji = None
        if icon and icon.get("type") == "emoji":
            icon_emoji = icon.get("emoji")

        blocks = self.get_block_children_deep(page_id, max_depth=10)

        # Guardrail: refuse if there are nested child_page blocks anywhere.
        def has_child_page(block_list: list) -> bool:
            for b in block_list:
                if b.get("type") == "child_page":
                    return True
                if b.get("children") and has_child_page(b["children"]):
                    return True
            return False

        if has_child_page(blocks):
            raise ValueError(
                f"Cannot copy page {page_id}: the page (or a descendant) "
                f"contains nested child_page blocks. Notion's API does not "
                f"support recreating nested pages inline. Copy nested pages "
                f"manually or use the Notion UI drag-and-drop."
            )

        write_blocks: list[dict] = []
        for block in blocks:
            converted = self._convert_block_for_copy(block)
            if converted:
                write_blocks.append(converted)

        skipped = self.unsupported_block_types(blocks, self._convert_block_for_copy)

        new_page = self.create_page(
            parent_id=new_parent_id,
            title=title,
            parent_type=parent_type,
            children=None,
            icon=icon_emoji,
        )
        new_page_id = new_page["id"]

        try:
            if write_blocks:
                self.append_tree(new_page_id, write_blocks)
        except Exception:
            # Do not leave a half-written copy behind; the original is intact.
            try:
                self._patch(f"/pages/{new_page_id}", {"archived": True})
            except NotionAPIError:
                pass
            raise

        archived = archive_original and not skipped
        if archived:
            self._patch(f"/pages/{page_id}", {"archived": True})

        result = {
            "status": "ok",
            "new_page_id": new_page_id,
            "new_page_url": new_page.get("url", ""),
            "original_page_id": page_id,
            "original_archived": archived,
            "blocks_copied": len(write_blocks),
            "skipped": dict(skipped),
        }
        if skipped and archive_original:
            result["warning"] = (
                "Some blocks could not be copied (see 'skipped'), so the "
                "original page was left in place."
            )
        return result

    @classmethod
    def _convert_block_for_copy(cls, block: dict) -> dict | None:
        """Extend convert_block_for_write with image/file/bookmark support.

        Used by copy_page_to_parent. For file_upload-hosted media the block
        is downgraded to an ``external`` reference using the current (signed,
        short-lived) URL. The caller is expected to re-upload permanent
        copies via upload_image_as_block after the copy completes.
        """
        btype = block.get("type", "")
        bdata = block.get(btype, {})

        if btype == "image":
            itype = bdata.get("type", "")
            url = ""
            if itype == "external":
                url = bdata.get("external", {}).get("url", "")
            elif itype == "file":
                url = bdata.get("file", {}).get("url", "")
            elif itype == "file_upload":
                fu = bdata.get("file_upload", {})
                # Some responses embed the url under .file.url; fall back to .url
                url = (
                    fu.get("file", {}).get("url", "")
                    if isinstance(fu.get("file"), dict)
                    else ""
                )
            if not url:
                return None
            result: dict = {
                "object": "block",
                "type": "image",
                "image": {"type": "external", "external": {"url": url}},
            }
            if bdata.get("caption"):
                result["image"]["caption"] = cls.convert_rich_text_for_write(
                    bdata["caption"]
                )
            return result

        if btype == "file":
            ftype = bdata.get("type", "")
            url = ""
            if ftype == "external":
                url = bdata.get("external", {}).get("url", "")
            elif ftype == "file":
                url = bdata.get("file", {}).get("url", "")
            if not url:
                return None
            result = {
                "object": "block",
                "type": "file",
                "file": {"type": "external", "external": {"url": url}},
            }
            if bdata.get("caption"):
                result["file"]["caption"] = cls.convert_rich_text_for_write(
                    bdata["caption"]
                )
            if bdata.get("name"):
                result["file"]["name"] = bdata["name"]
            return result

        # Fall back to the standard converter for everything else.
        return cls.convert_block_for_write(block)

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

    QUERY_MAX_ITEMS = 500

    def query_database(
        self,
        database_id: str,
        filter: dict | None = None,
        sorts: list | None = None,
        page_size: int = 100,
    ) -> list[dict]:
        """Query a database with optional filter and sort criteria.

        Handles pagination automatically, returning at most 500 rows. Use
        ``query_database_page`` to learn whether more rows exist.
        """
        return self.query_database_page(database_id, filter, sorts, page_size)[0]

    def query_database_page(
        self,
        database_id: str,
        filter: dict | None = None,
        sorts: list | None = None,
        page_size: int = 100,
    ) -> tuple[list[dict], bool]:
        """Like ``query_database`` but also returns whether rows were cut off."""
        results: list[dict] = []
        cursor = None
        while True:
            body: dict[str, Any] = {"page_size": max(1, min(page_size, 100))}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            data = self._post(f"/databases/{database_id}/query", body)
            results.extend(data.get("results", []))
            has_more = bool(data.get("has_more"))
            if not has_more or len(results) >= self.QUERY_MAX_ITEMS:
                break
            cursor = data.get("next_cursor")
        truncated = has_more or len(results) > self.QUERY_MAX_ITEMS
        return results[: self.QUERY_MAX_ITEMS], truncated

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
        body: dict[str, Any] = {
            "query": query,
            "page_size": max(1, min(page_size, 100)),
        }
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
        Subpages and inline databases are listed but not descended into.
        """
        if max_depth <= 0:
            return []
        blocks = self.get_block_children(block_id)
        for block in blocks:
            if block.get("has_children") and block.get("type") not in (
                "child_page", "child_database",
            ):
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

    def prepend_children(self, parent_id: str, children: list[dict]) -> list[dict]:
        """Insert up to 100 blocks at the start of a parent's children."""
        data = self._patch(
            f"/blocks/{parent_id}/children",
            {"children": children[:BATCH_SIZE], "position": {"type": "start"}},
        )
        return data.get("results", [])

    @staticmethod
    def _inline_levels_needed(block: dict) -> int:
        """Levels of children a block must carry inline when it is created."""
        return {"table": 1, "column_list": 2, "column": 1}.get(block.get("type"), 0)

    @classmethod
    def _prune_for_request(cls, block: dict, depth: int = 0) -> tuple[dict, list]:
        """Trim a write-format block to what one append request may carry.

        Returns the trimmed block and a list of ``(path, children)`` pairs:
        children that must be appended afterwards to the descendant reached
        by following ``path`` (child indexes) from this block.
        """
        btype = block.get("type", "")
        data = block.get(btype) or {}
        kids = data.get("children") or []
        if not kids:
            return block, []

        def without_children() -> dict:
            return {**block, btype: {k: v for k, v in data.items() if k != "children"}}

        too_deep = depth >= MAX_NESTING or any(
            depth + 1 + cls._inline_levels_needed(k) > MAX_NESTING for k in kids
        )
        if too_deep and not cls._inline_levels_needed(block):
            return without_children(), [((), kids)]

        keep, extra = kids[:BATCH_SIZE], kids[BATCH_SIZE:]
        pending: list = [((), extra)] if extra else []
        new_kids = []
        for j, kid in enumerate(keep):
            pruned, kid_pending = cls._prune_for_request(kid, depth + 1)
            new_kids.append(pruned)
            pending.extend(((j, *path), ch) for path, ch in kid_pending)
        return {**block, btype: {**data, "children": new_kids}}, pending

    def append_tree(self, parent_id: str, blocks: list[dict]) -> list[dict]:
        """Append write-format blocks of any depth or size.

        Notion accepts at most two levels of nested children and 100 children
        per list in one request. Anything beyond that is appended in follow-up
        requests. Returns the created top-level blocks.
        """
        payload, pending = [], []
        for block in blocks:
            pruned, block_pending = self._prune_for_request(block)
            payload.append(pruned)
            pending.append(block_pending)

        created = self.append_children(parent_id, payload)
        for top, block_pending in zip(created, pending):
            for path, children in block_pending:
                target_id = top["id"]
                for index in path:
                    target_id = self.get_block_children(target_id)[index]["id"]
                self.append_tree(target_id, children)
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

    # ── File Uploads ─────────────────────────────────────────────────

    def create_file_upload(
        self,
        filename: str | None = None,
        content_type: str | None = None,
        mode: str = "single_part",
    ) -> dict:
        """Create a file_upload object to receive an upload.

        Returns the file_upload with an ``id`` that is then used with
        ``send_file_upload`` to transmit the binary payload.
        """
        body: dict[str, Any] = {"mode": mode}
        if filename:
            body["filename"] = filename
        if content_type:
            body["content_type"] = content_type
        return self._post("/file_uploads", body)

    def send_file_upload(self, upload_id: str, file_path: str) -> dict:
        """Upload binary data to a previously-created file_upload.

        Uses multipart/form-data (not JSON), so bypasses the default
        ``Content-Type: application/json`` header on ``self._http``.
        """
        path = resolve_local_path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content_type, _ = mimetypes.guess_type(str(path))
        if not content_type:
            content_type = "application/octet-stream"

        with path.open("rb") as f:
            files = {"file": (path.name, f, content_type)}
            # Fresh httpx call without the JSON Content-Type from self._http.
            r = httpx.post(
                f"{NOTION_API_BASE}/file_uploads/{upload_id}/send",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": NOTION_VERSION,
                },
                files=files,
                timeout=120.0,
            )
        _raise_for_error(r, "POST", f"/file_uploads/{upload_id}/send")
        time.sleep(RATE_LIMIT_DELAY)
        return r.json()

    def upload_file(self, file_path: str) -> dict:
        """Complete file upload: create + send. Returns the final file_upload object.

        Use the returned ``id`` in image/file/pdf blocks via ``make_image_block``
        or by constructing a block with ``{"type": "file_upload", "file_upload": {"id": ...}}``.
        """
        path = resolve_local_path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        content_type, _ = mimetypes.guess_type(str(path))
        upload = self.create_file_upload(
            filename=path.name,
            content_type=content_type,
        )
        self.send_file_upload(upload["id"], str(path))
        # Re-fetch to get the uploaded state (status, etc.)
        return self._get(f"/file_uploads/{upload['id']}")

    def get_file_upload(self, upload_id: str) -> dict:
        """Get the current state of a file_upload (status, URLs, etc.)."""
        return self._get(f"/file_uploads/{upload_id}")

    # ── Block Builders for Media ─────────────────────────────────────

    @staticmethod
    def make_image_block(
        file_upload_id: str | None = None,
        external_url: str | None = None,
        caption: str = "",
    ) -> dict:
        """Build an image block using either a Notion file_upload or an external URL.

        Exactly one of ``file_upload_id`` or ``external_url`` must be provided.
        """
        if file_upload_id and external_url:
            raise ValueError("Provide only one of file_upload_id or external_url")
        if not file_upload_id and not external_url:
            raise ValueError("Must provide file_upload_id or external_url")

        if file_upload_id:
            image: dict[str, Any] = {
                "type": "file_upload",
                "file_upload": {"id": file_upload_id},
            }
        else:
            image = {
                "type": "external",
                "external": {"url": external_url},
            }

        if caption:
            image["caption"] = [
                {"type": "text", "text": {"content": caption}}
            ]

        return {"object": "block", "type": "image", "image": image}

    @staticmethod
    def make_file_block(
        file_upload_id: str | None = None,
        external_url: str | None = None,
        caption: str = "",
        name: str = "",
    ) -> dict:
        """Build a file block (PDF, generic file attachment)."""
        if file_upload_id and external_url:
            raise ValueError("Provide only one of file_upload_id or external_url")
        if not file_upload_id and not external_url:
            raise ValueError("Must provide file_upload_id or external_url")

        if file_upload_id:
            f: dict[str, Any] = {
                "type": "file_upload",
                "file_upload": {"id": file_upload_id},
            }
        else:
            f = {"type": "external", "external": {"url": external_url}}

        if caption:
            f["caption"] = [{"type": "text", "text": {"content": caption}}]
        if name:
            f["name"] = name

        return {"object": "block", "type": "file", "file": f}

    def download_file_from_url(self, url: str, output_path: str) -> dict:
        """Download a file from any URL (external or Notion-hosted signed) to disk."""
        out = resolve_local_path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(url, timeout=120.0, follow_redirects=True)
        r.raise_for_status()
        out.write_bytes(r.content)
        return {"path": str(out), "size": len(r.content)}

    def get_image_url_from_block(self, block_id: str) -> str:
        """Extract the download URL from an image block (external, file, or file_upload)."""
        block = self.get_block(block_id)
        if block.get("type") != "image":
            raise ValueError(f"Block {block_id} is not an image block")
        image = block.get("image", {})
        itype = image.get("type", "")
        if itype == "external":
            return image.get("external", {}).get("url", "")
        if itype == "file":
            # Notion-hosted signed URL
            return image.get("file", {}).get("url", "")
        if itype == "file_upload":
            # Fetch the file_upload object to get a download URL
            upload_id = image.get("file_upload", {}).get("id", "")
            if not upload_id:
                return ""
            info = self.get_file_upload(upload_id)
            # file_upload objects expose the URL under `file.url` once completed
            return info.get("file", {}).get("url", "") or info.get("url", "")
        return ""

    # ── Table Row Operations ─────────────────────────────────────────

    def insert_table_row_after(
        self,
        table_id: str,
        cells: list,
        after_row_id: str | None = None,
    ) -> dict:
        """Insert a new row into an existing table.

        Args:
            table_id: The table block ID.
            cells: List of cell contents. Each cell can be:
                - a plain string ("hello")  → single text segment
                - a list of strings (["bold", " normal"]) → multi-segment
                - a list of dicts → pre-built rich_text segments (advanced)
            after_row_id: If provided, insert after this specific row. Otherwise
                append to the end of the table.

        Returns the created row block.
        """
        table = self.get_block(table_id)
        if table.get("type") != "table":
            raise ValueError(f"Block {table_id} is not a table")

        table_width = table.get("table", {}).get("table_width")
        if table_width and len(cells) != table_width:
            raise ValueError(
                f"Table width is {table_width} but got {len(cells)} cells"
            )

        # Build rich_text cells
        row_cells: list[list[dict]] = []
        for cell in cells:
            if isinstance(cell, str):
                row_cells.append(
                    [{"type": "text", "text": {"content": cell}}]
                )
            elif isinstance(cell, list):
                segments: list[dict] = []
                for seg in cell:
                    if isinstance(seg, dict):
                        segments.append(seg)
                    else:
                        segments.append(
                            {"type": "text", "text": {"content": str(seg)}}
                        )
                row_cells.append(segments)
            else:
                row_cells.append(
                    [{"type": "text", "text": {"content": str(cell)}}]
                )

        row_block = {
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": row_cells},
        }

        # Determine where to insert
        if after_row_id is None:
            existing_rows = self.get_block_children(table_id)
            if not existing_rows:
                # Empty table — append directly
                created = self.append_children(table_id, [row_block])
                return created[0] if created else {}
            after_row_id = existing_rows[-1]["id"]

        created = self.insert_blocks_after(after_row_id, [row_block])
        return created[0] if created else {}

    def update_table_cell(
        self,
        row_id: str,
        cell_index: int,
        new_text: str,
    ) -> dict:
        """Replace the text of a single cell in a table row (plain-text shortcut)."""
        row = self.get_block(row_id)
        if row.get("type") != "table_row":
            raise ValueError(f"Block {row_id} is not a table_row")
        cells = row.get("table_row", {}).get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            raise ValueError(
                f"cell_index {cell_index} out of range (row has {len(cells)} cells)"
            )
        updated_cells = list(cells)
        updated_cells[cell_index] = [
            {"type": "text", "text": {"content": new_text}}
        ]
        return self.update_block(
            row_id, {"table_row": {"cells": updated_cells}}
        )

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

    @classmethod
    def make_rich_text_list(cls, content: str) -> list[dict]:
        """Plain text as rich text, split to respect Notion's 2000-char limit."""
        chunks = [
            content[i : i + MAX_TEXT_LENGTH]
            for i in range(0, len(content), MAX_TEXT_LENGTH)
        ] or [""]
        return [cls.make_rich_text(chunk) for chunk in chunks]

    WRITABLE_MENTION_TYPES = ("user", "page", "database", "date")

    @classmethod
    def convert_rich_text_for_write(cls, rich_texts: list[dict]) -> list[dict]:
        """Convert read-format rich_text to write-format.

        Keeps annotations and links. User, page, database and date mentions
        and inline equations are passed through as-is; other non-text
        segments (link previews, template mentions) fall back to plain text.
        """
        result = []
        for rt in rich_texts:
            rtype = rt.get("type", "text")
            item: dict[str, Any] | None = None
            if rtype == "equation" and rt.get("equation"):
                item = {"type": "equation", "equation": rt["equation"]}
            elif rtype == "mention":
                mention = rt.get("mention") or {}
                mtype = mention.get("type", "")
                if mtype in cls.WRITABLE_MENTION_TYPES and mention.get(mtype):
                    value = mention[mtype]
                    if mtype != "date":
                        value = {"id": value.get("id", "")}
                    item = {"type": "mention", "mention": {"type": mtype, mtype: value}}
            if item is None:
                item = {"type": "text", "text": {"content": rt.get("plain_text", "")}}
                link = (rt.get("text") or {}).get("link")
                if not link and rtype != "text" and rt.get("href"):
                    link = {"url": rt["href"]}
                if link:
                    item["text"]["link"] = link
            if rt.get("annotations"):
                item["annotations"] = rt["annotations"]
            result.append(item)
        return result

    # ── Block Conversion (read → write) ──────────────────────────────

    RICH_TEXT_BLOCK_TYPES = {
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item", "quote",
        "callout", "toggle", "to_do",
    }

    MEDIA_BLOCK_TYPES = ("image", "file", "video", "pdf", "audio")

    @classmethod
    def unsupported_block_types(cls, blocks: list[dict], converter=None) -> Counter:
        """Count blocks (at any depth) that ``converter`` cannot recreate."""
        converter = converter or cls.convert_block_for_write
        skipped: Counter = Counter()
        for block in blocks:
            shallow = {k: v for k, v in block.items() if k != "children"}
            if converter(shallow) is None:
                skipped[block.get("type", "unknown")] += 1
            elif block.get("children"):
                skipped += cls.unsupported_block_types(block["children"], converter)
        return skipped

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
            if btype.startswith("heading_") and bdata.get("is_toggleable"):
                result[btype]["is_toggleable"] = True

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

        elif btype == "equation":
            return {
                "object": "block",
                "type": "equation",
                "equation": {"expression": bdata.get("expression", "")},
            }

        elif btype in ("bookmark", "embed"):
            if not bdata.get("url"):
                return None
            result = {"object": "block", "type": btype, btype: {"url": bdata["url"]}}
            if bdata.get("caption"):
                result[btype]["caption"] = cls.convert_rich_text_for_write(
                    bdata["caption"]
                )
            return result

        elif btype in cls.MEDIA_BLOCK_TYPES:
            # Only externally hosted media can be recreated from a read.
            url = (bdata.get("external") or {}).get("url", "")
            if bdata.get("type") != "external" or not url:
                return None
            result = {
                "object": "block",
                "type": btype,
                btype: {"type": "external", "external": {"url": url}},
            }
            if bdata.get("caption"):
                result[btype]["caption"] = cls.convert_rich_text_for_write(
                    bdata["caption"]
                )
            return result

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
                    "rich_text": cls.make_rich_text_list(text),
                    "language": kwargs.get("language", "plain text"),
                },
            }

        if block_type in cls.RICH_TEXT_BLOCK_TYPES:
            block_data: dict[str, Any] = {
                "rich_text": cls.make_rich_text_list(text),
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
                    "rich_text": self.make_rich_text_list(new_text),
                }
            })
        elif btype == "code":
            return self.update_block(block_id, {
                "code": {
                    "rich_text": self.make_rich_text_list(new_text),
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
    ) -> tuple[list[dict], int]:
        """Replace old_text with new_text in a list of rich_text segments, preserving formatting.

        Returns a tuple of (updated_segments, replacement_count) where replacement_count
        is the total number of occurrences of old_text that were replaced across all segments.
        """
        updated = []
        total_replacements = 0
        for rt in rich_texts:
            new_rt = self.convert_rich_text_for_write([rt])[0]
            if new_rt["type"] == "text" and old_text:
                original = new_rt["text"]["content"]
                total_replacements += original.count(old_text)
                new_rt["text"]["content"] = original.replace(old_text, new_text)
            updated.append(new_rt)
        return updated, total_replacements

    def find_and_replace_text(
        self, block_id: str, old_text: str, new_text: str
    ) -> dict:
        """Find and replace text within a block's rich_text, preserving formatting.

        Supports all rich_text block types plus table_row cells.

        Raises ValueError if old_text does not appear in the block's text.
        The error message includes the block's current plain text (up to 500 chars)
        so the caller can diagnose Unicode / whitespace / formatting mismatches
        (e.g., en-dash U+2013 vs hyphen-minus U+002D, curly vs straight quotes,
        non-breaking vs regular spaces).

        Returns the block-update result dict with an added ``replacements`` key
        containing the total number of occurrences of old_text that were replaced.
        """
        block = self.get_block(block_id)
        btype = block.get("type", "")
        bdata = block.get(btype, {})

        if btype == "table_row":
            cells = bdata.get("cells", [])
            updated_cells = []
            total_replacements = 0
            for cell in cells:
                new_cell, cell_count = self._replace_in_rich_texts(
                    cell, old_text, new_text
                )
                updated_cells.append(new_cell)
                total_replacements += cell_count

            if total_replacements == 0:
                # Build a preview of the current plain text across all cells
                current_text = " | ".join(
                    "".join(seg.get("plain_text", "") for seg in cell)
                    for cell in cells
                )
                raise ValueError(
                    f"find_and_replace_text: old_text not found in block {block_id} "
                    f"(table_row, {len(cells)} cells). "
                    f"Current text preview (up to 500 chars): {current_text[:500]!r}. "
                    f"Searched for: {old_text!r}. "
                    f"This often indicates a Unicode character mismatch (en-dash vs hyphen, "
                    f"curly vs straight quotes, non-breaking space vs regular space). "
                    f"Consider using update_block_text or update_table_cell for full replacement, "
                    f"or copy the exact string from the preview above."
                )

            result = self.update_block(
                block_id, {"table_row": {"cells": updated_cells}}
            )
            result["replacements"] = total_replacements
            return result

        rich_texts = bdata.get("rich_text", [])
        updated, total_replacements = self._replace_in_rich_texts(
            rich_texts, old_text, new_text
        )

        if total_replacements == 0:
            current_text = "".join(seg.get("plain_text", "") for seg in rich_texts)
            raise ValueError(
                f"find_and_replace_text: old_text not found in block {block_id} "
                f"(type={btype!r}). "
                f"Current text preview (up to 500 chars): {current_text[:500]!r}. "
                f"Searched for: {old_text!r}. "
                f"This often indicates a Unicode character mismatch (en-dash vs hyphen, "
                f"curly vs straight quotes, non-breaking space vs regular space). "
                f"Consider using update_block_text for full replacement, "
                f"or copy the exact string from the preview above."
            )

        result = self.update_block_rich_text(block_id, updated)
        result["replacements"] = total_replacements
        return result

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
            path = resolve_local_path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        return snapshot

    def restore_page_from_snapshot(
        self, page_id: str, snapshot: dict | str
    ) -> dict:
        """Restore a page's content from a snapshot without risking data loss.

        The snapshot content is written first; the page's previous blocks are
        deleted only after that write succeeds. If the write fails, the new
        blocks are removed and the page is left as it was.

        Blocks that cannot be recreated through the API (subpages, inline
        databases, Notion-hosted files, synced blocks) are never deleted from
        the page: they stay, above the restored content, and are counted in
        ``preserved``. Snapshot blocks that cannot be recreated are counted
        in ``skipped``.

        Args:
            page_id: The page to restore into.
            snapshot: Either a snapshot dict or a file path to a JSON snapshot.

        Returns:
            Summary of the restoration.
        """
        if isinstance(snapshot, str):
            snapshot = json.loads(
                resolve_local_path(snapshot).read_text(encoding="utf-8")
            )

        snapshot_blocks = snapshot.get("blocks", [])
        write_blocks = [
            converted
            for block in snapshot_blocks
            if (converted := self.convert_block_for_write(block))
        ]
        skipped = self.unsupported_block_types(snapshot_blocks)

        current = self.get_block_children_deep(page_id)
        current_ids = {block["id"] for block in current}
        preserved: Counter = Counter()
        to_delete: list[str] = []
        for block in current:
            unsupported = self.unsupported_block_types([block])
            if unsupported:
                preserved += unsupported
            else:
                to_delete.append(block["id"])

        try:
            created = self.append_tree(page_id, write_blocks)
        except Exception:
            # Roll back whatever was written; the previous content is intact.
            for block in self.get_block_children(page_id):
                if block["id"] not in current_ids:
                    try:
                        self.delete_block(block["id"])
                    except NotionAPIError:
                        pass
            raise

        deleted = 0
        for block_id in to_delete:
            try:
                self.delete_block(block_id)
                deleted += 1
            except NotionAPIError:
                pass  # Already deleted or archived

        return {
            "status": "ok",
            "deleted": deleted,
            "created": len(created),
            "preserved": dict(preserved),
            "skipped": dict(skipped),
            "page_id": page_id,
        }

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
            elif btype in ("child_page", "child_database"):
                kind = "page" if btype == "child_page" else "database"
                lines.append(f"{indent}[{kind}: {bdata.get('title', '')}]")
            elif btype == "equation":
                lines.append(f"{indent}$$ {bdata.get('expression', '')} $$")
            elif btype in ("bookmark", "embed"):
                lines.append(f"{indent}[{btype}]({bdata.get('url', '')})")
            elif btype in NotionClient.MEDIA_BLOCK_TYPES:
                source = bdata.get(bdata.get("type", ""), {}) or {}
                caption = "".join(
                    rt.get("plain_text", "") for rt in bdata.get("caption", [])
                )
                label = caption or btype
                prefix = "!" if btype == "image" else ""
                lines.append(f"{indent}{prefix}[{label}]({source.get('url', '')})")
            else:
                if text:
                    lines.append(f"{indent}{text}")

            if block.get("children"):
                lines.append(
                    NotionClient.blocks_to_markdown(block["children"], depth + 1)
                )

        return "\n".join(lines)
