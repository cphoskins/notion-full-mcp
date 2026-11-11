"""Tool-layer tests: every MCP tool against a mocked client."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from notion_mcp import server
from notion_mcp.client import NotionAPIError, NotionClient
from tests.conftest import para, text

ROW = {
    "id": "row1",
    "object": "page",
    "url": "https://notion.so/row1",
    "last_edited_time": "2026-01-01",
    "parent": {"database_id": "db"},
    "properties": {"Task name": {"type": "title", "title": [{"plain_text": "Call Boris"}]}},
}


def tool(tool_name: str):
    t = getattr(server, tool_name)
    return getattr(t, "fn", t)


def run(tool_name: str, /, *args, **kwargs):
    out = tool(tool_name)(*args, **kwargs)
    try:
        return json.loads(out)
    except ValueError:
        return out


@pytest.fixture
def nc(client):
    """A real NotionClient (HTTP mocked) installed as the server's client."""
    server._client = client
    yield client
    server._client = None


def test_all_tools_registered_and_documented():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    listed = {t["name"] for t in json.load(open("server.json"))["tools"]}
    assert names == listed
    assert all(t.description for t in tools)


def test_get_client_requires_token(monkeypatch):
    server._client = None
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    assert run("get_self")["status"] == "error"
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    with patch("notion_mcp.client.httpx.Client"):
        assert isinstance(server._get_client(), NotionClient)
    server._client = None


class TestSearchAndInfo:
    def test_titles_for_database_rows(self, nc):
        nc.search_pages = MagicMock(return_value=[ROW])
        nc.search = MagicMock(return_value=[ROW, {"id": "db", "object": "database", "title": [{"plain_text": "Todo List"}]}])
        nc.get_page = MagicMock(return_value=ROW)
        assert run("search_pages", "boris")[0]["title"] == "Call Boris"
        both = run("search", "x", filter_type="bogus")
        assert [b["title"] for b in both] == ["Call Boris", "Todo List"]
        assert nc.search.call_args.kwargs["filter_type"] is None
        assert run("get_page_info", "row1")["title"] == "Call Boris"

    def test_get_database_and_query(self, nc):
        nc.get_database = MagicMock(return_value={"id": "db", "title": [{"plain_text": "Todo"}], "properties": {"Task name": {"type": "title"}}})
        assert run("get_database", "db")["properties"] == {"Task name": "title"}
        nc.query_database_page = MagicMock(return_value=([ROW], True))
        out = run("query_database", "db", filter_json='{"a": 1}', sorts_json='[{"b": 2}]')
        assert out["count"] == 1 and out["truncated"] is True
        assert nc.query_database_page.call_args.kwargs["filter"] == {"a": 1}

    def test_users_and_block(self, nc):
        nc.get_self = MagicMock(return_value={"type": "bot"})
        nc.list_users = MagicMock(return_value=[{"id": "u", "name": "Ann", "type": "person"}])
        nc.get_block = MagicMock(return_value={"id": "b"})
        assert run("get_self")["type"] == "bot"
        assert run("list_users")[0]["name"] == "Ann"
        assert run("get_block", "b")["id"] == "b"


class TestReading:
    def test_read_page_deep_formats(self, nc):
        nc.get_block_children_deep = MagicMock(return_value=[para("hello", "b1")])
        assert run("read_page_deep", "p") == "hello"
        assert run("read_page_deep", "p", format="json")[0]["id"] == "b1"

    def test_list_block_children_text(self, nc):
        nc.get_block_children = MagicMock(return_value=[
            para("x" * 300, "b1"),
            {"id": "r", "type": "table_row", "table_row": {"cells": [[text("a")], [text("b")]]}},
            {"id": "s", "type": "child_page", "child_page": {"title": "Sub"}},
        ])
        out = run("list_block_children", "p")
        assert len(out[0]["text"]) == 200 and out[1]["text"] == "a | b" and out[2]["text"] == "Sub"
        assert "text" not in run("list_block_children", "p", include_text=False)[0]


class TestEditing:
    def test_update_and_rich_text(self, nc):
        nc.update_block_text = MagicMock(return_value={"id": "b"})
        nc.update_block_rich_text = MagicMock(return_value={"id": "b"})
        assert run("update_block_text", "b", "new")["block_id"] == "b"
        assert run("update_block_rich_text", "b", '[{"type":"text","text":{"content":"x"}}]')["status"] == "ok"
        assert run("update_block_rich_text", "b", "not json")["status"] == "error"

    def test_find_replace(self, nc):
        nc.find_and_replace_text = MagicMock(return_value={"id": "b", "replacements": 2})
        assert run("find_replace_in_block", "b", "a", "b")["replacements"] == 2
        nc.find_and_replace_text = MagicMock(side_effect=ValueError("not found"))
        assert run("find_replace_in_block", "b", "a", "b")["error_type"] == "text_not_found"
        nc.find_and_replace_text = MagicMock(side_effect=NotionAPIError(404, "object_not_found", "gone", "GET", "/blocks/b"))
        out = run("find_replace_in_block", "b", "a", "b")
        assert "error_type" not in out and "gone" in out["message"]

    def test_inserts(self, nc):
        nc.insert_blocks_after = MagicMock(return_value=[{"id": "n1"}, {"id": "n2"}])
        nc.append_children = MagicMock(return_value=[{"id": "n1"}])
        blocks = json.dumps([para("a"), para("b")])
        assert run("insert_blocks_after", "b", blocks)["block_ids"] == ["n1", "n2"]
        assert run("insert_text_after", "b", "hi", block_type="heading_2")["block_id"] == "n1"
        assert nc.insert_blocks_after.call_args.args[1][0]["type"] == "heading_2"
        assert run("insert_text_after", "b", "hi", block_type="table")["status"] == "error"
        assert run("append_blocks_to_page", "p", blocks)["created_count"] == 1

    def test_tables(self, nc):
        nc.insert_table_row_after = MagicMock(return_value={"id": "r"})
        nc.update_table_cell = MagicMock(return_value={"id": "r"})
        assert run("insert_table_row", "t", '["a", "b"]')["block_id"] == "r"
        assert nc.insert_table_row_after.call_args.kwargs["after_row_id"] is None
        assert run("insert_table_row", "t", '{"a": 1}')["status"] == "error"
        assert run("update_table_cell", "r", 0, "x")["status"] == "ok"

    def test_delete(self, nc):
        nc.delete_block = MagicMock()
        assert run("delete_block", "b") == {"status": "ok", "deleted": "b"}


class TestMedia:
    def test_upload_tools(self, nc):
        nc.upload_file = MagicMock(return_value={"id": "up", "filename": "a.png", "content_type": "image/png", "status": "uploaded"})
        nc.insert_blocks_after = MagicMock(return_value=[{"id": "blk"}])
        nc.append_children = MagicMock(return_value=[{"id": "blk"}])
        assert run("upload_file", "/tmp/a.png")["upload_status"] == "uploaded"
        assert run("upload_image_as_block", "/tmp/a.png")["status"] == "error"
        assert run("upload_image_as_block", "/tmp/a.png", after_block_id="b", caption="c")["block_id"] == "blk"
        assert run("upload_image_as_block", "/tmp/a.png", parent_id="p")["file_upload_id"] == "up"
        assert run("upload_file_as_block", "/tmp/a.pdf")["status"] == "error"
        assert run("upload_file_as_block", "/tmp/a.pdf", after_block_id="b")["block_id"] == "blk"
        assert run("upload_file_as_block", "/tmp/a.pdf", parent_id="p", name="Report")["block_id"] == "blk"
        assert nc.append_children.call_args.args[1][0]["file"]["name"] == "Report"

    def _image(self, nc, siblings):
        nc.get_block = MagicMock(return_value={"type": "image", "parent": {"page_id": "par"}})
        nc.get_block_children = MagicMock(return_value=siblings)
        nc.upload_file = MagicMock(return_value={"id": "up"})
        nc.delete_block = MagicMock()
        nc.prepend_children = MagicMock(return_value=[{"id": "new"}])
        nc.insert_blocks_after = MagicMock(return_value=[{"id": "new"}])

    def test_replace_first_image_stays_first(self, nc):
        self._image(nc, [{"id": "img"}, {"id": "other"}])
        out = run("replace_image_block", "img", "/tmp/b.png")
        assert out["new_block_id"] == "new" and "warning" not in out
        nc.prepend_children.assert_called_once()
        nc.delete_block.assert_called_once_with("img")

    def test_replace_later_image_goes_after_previous_sibling(self, nc):
        self._image(nc, [{"id": "first"}, {"id": "img"}])
        run("replace_image_block", "img", "/tmp/b.png")
        assert nc.insert_blocks_after.call_args.args[0] == "first"

    def test_replace_image_guards(self, nc):
        self._image(nc, [{"id": "other"}])
        assert "not found" in run("replace_image_block", "img", "/tmp/b.png")["message"]
        nc.get_block = MagicMock(return_value={"type": "image", "parent": {"workspace": True}})
        assert "parent" in run("replace_image_block", "img", "/tmp/b.png")["message"]
        nc.get_block = MagicMock(return_value={"type": "paragraph"})
        assert "not an image" in run("replace_image_block", "img", "/tmp/b.png")["message"]
        nc.delete_block.assert_not_called()

    def test_download(self, nc):
        nc.get_image_url_from_block = MagicMock(return_value="https://u")
        nc.download_file_from_url = MagicMock(return_value={"path": "/tmp/x.png", "size": 3})
        assert run("download_image_block", "b", "/tmp/x.png")["size"] == 3
        nc.get_image_url_from_block = MagicMock(return_value="")
        assert run("download_image_block", "b", "/tmp/x.png")["status"] == "error"


class TestPages:
    def test_create_page(self, nc):
        nc.create_page = MagicMock(return_value=ROW)
        out = run("create_page", "db", "Call Boris", parent_type="database_id", icon_emoji="📞", children_json=json.dumps([para("x")]), properties_json='{"Status": {"status": {"name": "Done"}}}')
        assert out["title"] == "Call Boris"
        kw = nc.create_page.call_args.kwargs
        assert kw["icon"] == "📞" and kw["extra_properties"]["Status"] and len(kw["children"]) == 1

    def test_update_page_never_unarchives(self, nc):
        nc.update_page = MagicMock(return_value={"id": "p"})
        run("update_page", "p", title="T", properties_json='{"Status": {}}')
        kw = nc.update_page.call_args.kwargs
        assert set(kw["properties"]) == {"title", "Status"} and kw["archived"] is None and kw["icon"] is None
        run("update_page", "p", properties_json='{"Status": {}}', archived=True, icon_emoji="x")
        kw = nc.update_page.call_args.kwargs
        assert kw["properties"] == {"Status": {}} and kw["archived"] is True and kw["icon"] == "x"

    def test_move_restore_copy(self, nc):
        nc._post = MagicMock(return_value={"id": "p", "parent": {"page_id": "par"}})
        assert run("move_page", "p", "par")["parent"] == {"page_id": "par"}
        assert run("move_page", "p", "db", parent_type="database_id")["status"] == "error"
        nc.restore_page = MagicMock(return_value={"id": "p", "archived": False})
        assert run("restore_page", "p")["archived"] is False
        nc.copy_page_to_parent = MagicMock(return_value={"status": "ok", "skipped": {}})
        assert run("copy_page_to_parent", "p", "par", archive_original=False)["status"] == "ok"
        assert nc.copy_page_to_parent.call_args.kwargs["archive_original"] is False

    def test_comments(self, nc):
        nc.create_comment = MagicMock(return_value={"id": "c"})
        nc.list_comments = MagicMock(return_value=[{"id": "c", "rich_text": [text("hi")], "created_time": "t"}])
        assert run("create_comment", "p", "hi")["id"] == "c"
        assert run("list_comments", "p")[0]["text"] == "hi"


class TestSnapshotTools:
    def test_snapshot_default_path_and_count(self, nc, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        nc.get_page = MagicMock(return_value={"id": "p"})
        nc.get_block_children_deep = MagicMock(return_value=[para("a", "b1", children=[para("b", "b2")])])
        out = run("snapshot_page", "p")
        assert out["block_count"] == 2
        assert out["snapshot_path"].startswith(str(tmp_path / "notion-snapshots"))
        assert json.load(open(out["snapshot_path"]))["page_id"] == "p"

    def test_snapshot_with_files_reports_counts(self, nc, tmp_path):
        nc.snapshot_page = MagicMock(return_value={"blocks": [], "files_saved": 2, "files_failed": 1})
        out = run("snapshot_page", "p", str(tmp_path / "s.json"), include_files=True)
        assert out["files_saved"] == 2 and out["files_failed"] == 1
        assert nc.snapshot_page.call_args.kwargs == {"include_files": True}

    def test_restore_tool(self, nc):
        nc.restore_page_from_snapshot = MagicMock(return_value={"status": "ok", "preserved": {}, "skipped": {}})
        assert run("restore_page_from_snapshot", "p", "/tmp/s.json")["status"] == "ok"


ALL_TOOL_CALLS = [
    ("search_pages", ("q",)), ("search", ("q",)), ("get_page_info", ("p",)),
    ("read_page_deep", ("p",)), ("list_block_children", ("p",)),
    ("update_block_text", ("b", "x")), ("find_replace_in_block", ("b", "a", "c")),
    ("update_block_rich_text", ("b", "[]")), ("insert_blocks_after", ("b", "[]")),
    ("insert_text_after", ("b", "x")), ("append_blocks_to_page", ("p", "[]")),
    ("upload_file", ("/tmp/a",)), ("upload_image_as_block", ("/tmp/a", "b")),
    ("replace_image_block", ("b", "/tmp/a")), ("download_image_block", ("b", "/tmp/o")),
    ("upload_file_as_block", ("/tmp/a", "b")), ("insert_table_row", ("t", "[]")),
    ("update_table_cell", ("r", 0, "x")), ("delete_block", ("b",)),
    ("snapshot_page", ("p", "/tmp/s.json")), ("restore_page_from_snapshot", ("p", "/tmp/s.json")),
    ("create_page", ("par", "T")), ("update_page", ("p",)), ("move_page", ("p", "par")),
    ("restore_page", ("p",)), ("copy_page_to_parent", ("p", "par")),
    ("create_comment", ("p", "x")), ("list_comments", ("p",)), ("get_database", ("d",)),
    ("query_database", ("d",)), ("get_self", ()), ("list_users", ()), ("get_block", ("b",)),
]


def test_error_table_covers_every_tool():
    listed = {t["name"] for t in json.load(open("server.json"))["tools"]}
    assert {name for name, _ in ALL_TOOL_CALLS} == listed


@pytest.mark.parametrize("name,args", ALL_TOOL_CALLS)
def test_every_tool_reports_notion_errors(name, args):
    """An API failure becomes a JSON error carrying Notion's own message."""
    boom = NotionAPIError(403, "restricted_resource", "Integration lacks access", "GET", "/x")
    failing = MagicMock(side_effect=boom)
    fake = MagicMock()
    for attr in dir(NotionClient):
        if not attr.startswith("__"):
            setattr(fake, attr, failing)
    server._client = fake
    try:
        out = run(name, *args)
    finally:
        server._client = None
    assert out["status"] == "error"
    assert "Integration lacks access" in out["message"]
