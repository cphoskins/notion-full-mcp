#!/usr/bin/env python3
"""Opt-in live check: runs every tool against a real Notion workspace.

Everything happens inside one scratch page created under the parent page you
name, and that scratch page is moved to trash at the end.

    export NOTION_API_TOKEN=...            # integration secret
    export NOTION_TEST_PARENT_PAGE=...     # page shared with the integration
    uv run python scripts/live_check.py

Exits non-zero if any check fails.
"""

import json
import os
import struct
import sys
import tempfile
import zlib
from collections import Counter

from notion_mcp import server
from notion_mcp.client import NotionClient

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. "
)
results: list[tuple[str, str, str]] = []


def record(name: str, ok, note: str = "", info: bool = False) -> None:
    status = "INFO" if info else ("PASS" if ok else "FAIL")
    results.append((name, status, note))
    print(f"[{status}] {name}{': ' + note if note else ''}", flush=True)


def tool(tool_name: str, /, *args, **kwargs):
    fn = getattr(server, tool_name)
    out = getattr(fn, "fn", fn)(*args, **kwargs)
    try:
        return json.loads(out)
    except ValueError:
        return out


def check(name: str):
    def decorator(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - report and continue to cleanup
            record(name, False, f"{type(e).__name__}: {str(e)[:300]}")
    return decorator


def para(content: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]}}


def bullets(depth: int, max_depth: int) -> dict:
    block = {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"L{depth}"}}]}}
    if depth < max_depth:
        block["bulleted_list_item"]["children"] = [bullets(depth + 1, max_depth)]
    return block


def write_png(path: str, rgb: bytes) -> None:
    w = h = 8
    raw = b"".join(b"\x00" + rgb * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def types(client: NotionClient, block_id: str) -> list[str]:
    return [b["type"] for b in client.get_block_children(block_id)]


def texts(client: NotionClient, block_id: str) -> list[str]:
    return ["".join(t["plain_text"] for t in b.get(b["type"], {}).get("rich_text", [])) for b in client.get_block_children(block_id)]


def bullet_chain(client: NotionClient, block_id: str) -> int:
    depth = 1
    while True:
        kids = client.get_block_children(block_id)
        if not kids:
            return depth
        block_id, depth = kids[0]["id"], depth + 1


def main() -> int:
    parent = os.environ.get("NOTION_TEST_PARENT_PAGE", "")
    if not parent or not os.environ.get("NOTION_API_TOKEN"):
        print(__doc__)
        return 2
    c = NotionClient()
    server._client = c
    tmp = tempfile.mkdtemp(prefix="notion-live-")
    scratch = None
    ids: dict[str, str] = {}
    try:
        r = tool("create_page", parent, "notion-full-mcp live check (temporary)", icon_emoji="🧪")
        if r.get("status") != "ok":
            print("Cannot create scratch page:", r)
            return 1
        scratch = r["id"]
        record("create_page returns title", r["title"] == "notion-full-mcp live check (temporary)", r["title"])

        @check("database row titles")
        def _():
            db = c._post("/databases", {"parent": {"type": "page_id", "page_id": scratch}, "is_inline": True, "title": [{"type": "text", "text": {"content": "Live check DB"}}], "properties": {"Task name": {"title": {}}}})
            ids["db"] = db["id"]
            row = tool("create_page", db["id"], "Lorem row", parent_type="database_id")
            record("create_page title for database row", row.get("title") == "Lorem row", repr(row.get("title")))
            record("get_page_info title for database row", tool("get_page_info", row["id"])["title"] == "Lorem row")
            q = tool("query_database", db["id"])
            record("query_database reports truncated flag", q.get("count") == 1 and q.get("truncated") is False, f"count={q.get('count')} truncated={q.get('truncated')}")
            record("get_database", tool("get_database", db["id"]).get("properties") == {"Task name": "title"})

        @check("content and edits")
        def _():
            blocks = [
                {"type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": "Lorem heading"}}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Lorem ipsum "}}, {"type": "text", "text": {"content": "dolor sit", "link": {"url": "https://example.com"}}, "annotations": {"bold": True}}, {"type": "text", "text": {"content": " amet 10–20 units."}}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Due ALPHA on "}}, {"type": "mention", "mention": {"type": "date", "date": {"start": "2026-12-01"}}}, {"type": "text", "text": {"content": " with "}}, {"type": "equation", "equation": {"expression": "E=mc^2"}}]}},
                {"type": "to_do", "to_do": {"rich_text": [{"type": "text", "text": {"content": "consectetur"}}], "checked": True}},
                {"type": "code", "code": {"rich_text": [{"type": "text", "text": {"content": "a = 1"}}], "language": "python"}},
                {"type": "callout", "callout": {"rich_text": [{"type": "text", "text": {"content": "adipiscing"}}], "icon": {"type": "emoji", "emoji": "💡"}}},
                {"type": "toggle", "toggle": {"rich_text": [{"type": "text", "text": {"content": "elit"}}], "children": [para("inside toggle")]}},
                {"type": "equation", "equation": {"expression": "x^2"}},
                {"type": "bookmark", "bookmark": {"url": "https://example.com"}},
                {"type": "divider", "divider": {}},
            ]
            r = tool("append_blocks_to_page", scratch, json.dumps(blocks))
            record("append_blocks_to_page", r.get("created_count") == len(blocks), str(r.get("message", "")))
            h, p, m = r["block_ids"][:3]
            ids["h"] = h
            md = tool("read_page_deep", scratch)
            record("read_page_deep markdown", all(s in md for s in ("# Lorem heading", "inside toggle", "- [x] consectetur", "$$ x^2 $$", "[bookmark](https://example.com)", "[database: Live check DB]")), f"{len(md)} chars")
            r = tool("find_replace_in_block", p, "dolor sit", "DOLOR SIT")
            seg = [s for s in c.get_block(p)["paragraph"]["rich_text"] if s["plain_text"] == "DOLOR SIT"]
            record("find_replace keeps bold and link", r.get("replacements") == 1 and seg and seg[0]["annotations"]["bold"] and seg[0]["href"])
            record("find_replace no-match is an error", tool("find_replace_in_block", p, "10-20", "x").get("error_type") == "text_not_found")
            before = [s["type"] for s in c.get_block(m)["paragraph"]["rich_text"]]
            r = tool("find_replace_in_block", m, "ALPHA", "BETA")
            after = c.get_block(m)["paragraph"]["rich_text"]
            record("find_replace keeps mentions and equations", [s["type"] for s in after] == before and "BETA" in after[0]["plain_text"], f"{before} -> {[s['type'] for s in after]}")
            long_text = (LOREM * 40)[:4500]
            r = tool("update_block_text", h, long_text)
            got = "".join(s["plain_text"] for s in c.get_block(h)["heading_1"]["rich_text"])
            record("update_block_text beyond 2000 chars", r.get("status") == "ok" and got == long_text, r.get("message", f"{len(got)} chars"))
            tool("update_block_text", h, "Lorem heading")
            r = tool("update_block_rich_text", h, json.dumps([{"type": "text", "text": {"content": "Lorem heading"}, "annotations": {"italic": True}}]))
            record("update_block_rich_text", r.get("status") == "ok")
            a = tool("insert_text_after", h, "inserted")
            tool("insert_blocks_after", a["block_id"], json.dumps([para("ins A"), para("ins B")]))
            record("insert ordering", texts(c, scratch)[2:5] == ["inserted", "ins A", "ins B"], str(texts(c, scratch)[:6]))
            record("list_block_children / get_block", len(tool("list_block_children", scratch)) > 10 and tool("get_block", h).get("id"))
            e = tool("get_block", "00000000-0000-0000-0000-000000000000")
            record("errors carry Notion's message", "Notion API error 4" in e.get("message", "") and "object_not_found" in e.get("message", ""), e.get("message", "")[:140])

        @check("tables")
        def _():
            def row(*cells):
                return {"type": "table_row", "table_row": {"cells": [[{"type": "text", "text": {"content": x}}] for x in cells]}}
            r = tool("append_blocks_to_page", scratch, json.dumps([{"type": "table", "table": {"table_width": 2, "has_column_header": True, "has_row_header": False, "children": [row("Lorem", "Ipsum"), row("dolor", "sit")]}}]))
            tid = r["block_ids"][0]
            tool("insert_table_row", tid, json.dumps(["amet", "elit"]))
            tool("insert_table_row", tid, json.dumps(["sed", "do"]), after_row_id=c.get_block_children(tid)[0]["id"])
            listed = tool("list_block_children", tid)
            record("insert_table_row + row text in list_block_children", [e["text"] for e in listed] == ["Lorem | Ipsum", "sed | do", "dolor | sit", "amet | elit"], str([e["text"] for e in listed]))
            record("insert_table_row width validation", tool("insert_table_row", tid, json.dumps(["one"]))["status"] == "error")
            tool("update_table_cell", listed[2]["id"], 1, "SIT")
            r = tool("find_replace_in_block", listed[2]["id"], "dolor", "DOLOR")
            record("update_table_cell + find_replace on row", tool("list_block_children", tid)[2]["text"] == "DOLOR | SIT" and r.get("replacements") == 1)

        @check("uploads")
        def _():
            a, b, note = f"{tmp}/a.png", f"{tmp}/b.png", f"{tmp}/note.txt"
            write_png(a, b"\x2e\x8b\x57")
            write_png(b, b"\x8b\x2e\x57")
            with open(note, "w") as f:
                f.write(LOREM)
            media = tool("create_page", scratch, "media")["id"]
            ids["media"] = media
            r = tool("upload_image_as_block", a, parent_id=media, caption="lorem")
            tool("append_blocks_to_page", media, json.dumps([para("after image")]))
            d = tool("download_image_block", r["block_id"], f"{tmp}/dl.png")
            record("upload_image_as_block + download round-trip", d.get("status") == "ok" and open(f"{tmp}/dl.png", "rb").read() == open(a, "rb").read(), str(d.get("message", "")))
            r2 = tool("replace_image_block", r["block_id"], b)
            first = c.get_block_children(media)[0]
            record("replace_image_block keeps first position", r2.get("status") == "ok" and first["id"] == r2.get("new_block_id") and "warning" not in r2, str(r2.get("message", types(c, media))))
            r3 = tool("upload_file_as_block", note, parent_id=media)
            r4 = tool("upload_file", note)
            record("upload_file_as_block / upload_file", r3.get("status") == "ok" and r4.get("upload_status") == "uploaded", str(r3.get("message", "")))

        @check("comments and users")
        def _():
            tool("create_comment", scratch, "lorem comment")
            record("create_comment / list_comments", any(x["text"] == "lorem comment" for x in tool("list_comments", scratch)))
            record("get_self / list_users", tool("get_self").get("type") == "bot" and isinstance(tool("list_users"), list))
            record("search / search_pages", isinstance(tool("search", "live check"), list) and isinstance(tool("search_pages", "live check", max_results=500), list))

        @check("move, copy, trash")
        def _():
            a = tool("create_page", scratch, "page A", icon_emoji="🔬", children_json=json.dumps([para(LOREM), {"type": "equation", "equation": {"expression": "x^2"}}, {"type": "bookmark", "bookmark": {"url": "https://example.com"}}]))["id"]
            b = tool("create_page", scratch, "page B")["id"]
            ids["A"], ids["B"] = a, b
            r = tool("move_page", a, b)
            moved = c.get_page(a)["parent"].get("page_id", "").replace("-", "") == b.replace("-", "")
            record("move_page changes the parent", r.get("status") == "ok" and moved, str(r.get("message", "")))
            tool("move_page", a, scratch)
            source = Counter(types(c, a))
            r = tool("copy_page_to_parent", a, b)
            copy = Counter(types(c, r["new_page_id"]))
            record("copy_page_to_parent copies every block and archives original", source == copy and r["skipped"] == {} and c.get_page(a)["archived"] is True, f"source={dict(source)} copy={dict(copy)}")
            record("restore_page from trash", tool("restore_page", a).get("archived") is False and c.get_page(a)["archived"] is False)
            c._post("/databases", {"parent": {"type": "page_id", "page_id": a}, "is_inline": True, "title": [{"type": "text", "text": {"content": "Nested DB"}}], "properties": {"Name": {"title": {}}}})
            r = tool("copy_page_to_parent", a, b)
            record("copy with an uncopyable block keeps the original", r.get("skipped") == {"child_database": 1} and r.get("original_archived") is False and c.get_page(a)["archived"] is False, str({k: r.get(k) for k in ("skipped", "original_archived", "warning")}))
            r = tool("update_page", b, title="page B2", icon_emoji="📦")
            record("update_page title/icon", tool("get_page_info", b)["title"] == "page B2")

        @check("large structures")
        def _():
            deep = c.append_tree(scratch, [bullets(1, 5)])[0]["id"]
            record("append_tree writes 5 nested levels", bullet_chain(c, deep) == 5)
            table = {"type": "table", "table": {"table_width": 1, "has_column_header": False, "has_row_header": False, "children": [{"type": "table_row", "table_row": {"cells": [[{"type": "text", "text": {"content": f"row {i}"}}]]}} for i in range(150)]}}
            tid = c.append_tree(scratch, [table])[0]["id"]
            record("append_tree writes a 150-row table", len(c.get_block_children(tid)) == 150)

        @check("snapshot and restore")
        def _():
            snap_path = f"{tmp}/snap.json"
            s = tool("snapshot_page", scratch, snap_path)
            snapshot = json.load(open(snap_path))["blocks"]
            before_types = [b["type"] for b in snapshot]
            subpages = [k for k in ("media", "A", "B") if k in ids]
            record("snapshot_page", s.get("status") == "ok", f"{s.get('block_count')} blocks, top-level {dict(Counter(before_types))}")
            # Damage the page, then restore.
            kids = c.get_block_children(scratch)
            for b in [k for k in kids if k["type"] in ("paragraph", "table", "to_do")][:6]:
                tool("delete_block", b["id"])
            tool("append_blocks_to_page", scratch, json.dumps([para("JUNK added after snapshot")]))
            r = tool("restore_page_from_snapshot", scratch, snap_path)
            record("restore_page_from_snapshot succeeds", r.get("status") == "ok", str(r.get("message", {k: r.get(k) for k in ("created", "deleted", "preserved", "skipped")})))
            after_types = types(c, scratch)
            record("restore: nothing lost, junk removed", Counter(after_types) == Counter(before_types) and "JUNK added after snapshot" not in texts(c, scratch), f"before={dict(Counter(before_types))} after={dict(Counter(after_types))}")
            kept = [b for b in snapshot if not NotionClient.unsupported_block_types([b])]
            restorable_order = [t for t in after_types if t not in ("child_page", "child_database")]
            record("restore: content order matches snapshot", restorable_order == [b["type"] for b in kept])
            record("restore: subpages and databases untouched", not any(c.get_page(ids[k])["archived"] for k in subpages) and c.get_database(ids["db"]).get("archived") is False)
            deep = [b for b in c.get_block_children(scratch) if b["type"] == "bulleted_list_item"][-1]
            table = [b for b in c.get_block_children(scratch) if b["type"] == "table"][-1]
            record("restore: 5-level list and 150-row table intact", bullet_chain(c, deep["id"]) == 5 and len(c.get_block_children(table["id"])) == 150)
    finally:
        if scratch:
            c.update_page(scratch, archived=True)
            print(f"CLEANUP: scratch page {scratch} archived={c.get_page(scratch).get('archived')}")
    summary = Counter(status for _, status, _ in results)
    print("\nSUMMARY:", dict(summary))
    return 1 if summary.get("FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())
