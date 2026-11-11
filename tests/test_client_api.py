"""Client behaviour against a mocked HTTP layer."""

from unittest.mock import MagicMock

import pytest

from notion_mcp.client import (
    NotionAPIError,
    NotionClient,
    page_title,
    resolve_local_path,
)
from tests.conftest import para, response, text


def nested_bullets(depth: int, max_depth: int) -> dict:
    block = {
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [text(f"L{depth}")]},
    }
    if depth < max_depth:
        block["children"] = [nested_bullets(depth + 1, max_depth)]
    return block


def payload_depth(block: dict) -> int:
    kids = block[block["type"]].get("children", [])
    return 1 + max((payload_depth(k) for k in kids), default=0)


class FakeNotion:
    """Minimal in-memory stand-in for the block endpoints used by writers."""

    def __init__(self, client, existing=None, fail_on_call: int | None = None):
        self.children: dict[str, list[dict]] = {"page": list(existing or [])}
        self.requests: list[tuple[str, list[dict]]] = []
        self.deleted: list[str] = []
        self.fail_on_call = fail_on_call
        self.counter = 0
        client.append_children = self.append
        client.get_block_children = lambda bid: list(self.children.get(bid, []))
        client.get_block_children_deep = lambda bid, max_depth=10: list(self.children.get(bid, []))
        client.delete_block = self.delete

    def _store(self, parent, block):
        self.counter += 1
        new_id = f"new{self.counter}"
        stored = {**block, "id": new_id}
        self.children.setdefault(parent, []).append(stored)
        for kid in block[block["type"]].get("children", []) or []:
            self._store(new_id, kid)
        return stored

    def append(self, parent, blocks):
        self.requests.append((parent, blocks))
        if self.fail_on_call == len(self.requests):
            raise NotionAPIError(400, "validation_error", "boom", "PATCH", "/x")
        for b in blocks:
            assert payload_depth(b) <= 3, "more than two nested levels in one request"
            assert len(b[b["type"]].get("children", []) or []) <= 100
        return [self._store(parent, b) for b in blocks]

    def delete(self, block_id):
        self.deleted.append(block_id)
        for kids in self.children.values():
            kids[:] = [k for k in kids if k["id"] != block_id]

    def texts(self, parent="page"):
        out = []
        for b in self.children.get(parent, []):
            rich = b[b["type"]].get("rich_text", [])
            out.append("".join(r.get("plain_text") or r["text"]["content"] for r in rich))
        return out


class TestErrorsAndRetry:
    def test_error_carries_notion_message(self, client):
        client._http.request.return_value = response(
            400, {"code": "validation_error", "message": "content.length should be <= 2000"}
        )
        with pytest.raises(NotionAPIError) as exc:
            client.get_block("abc")
        assert "content.length should be <= 2000" in str(exc.value)
        assert "validation_error" in str(exc.value)
        assert exc.value.status == 400

    def test_error_without_json_body(self, client):
        import httpx

        client._http.request.return_value = httpx.Response(
            502, text="bad gateway", request=httpx.Request("GET", "https://x")
        )
        with pytest.raises(NotionAPIError) as exc:
            client.get_block("abc")
        assert "bad gateway" in str(exc.value)

    def test_retries_rate_limit_then_succeeds(self, client):
        client._http.request.side_effect = [
            response(429, {"code": "rate_limited"}, {"Retry-After": "2"}),
            response(200, {"id": "abc"}),
        ]
        assert client.get_block("abc") == {"id": "abc"}
        assert client._http.request.call_count == 2

    def test_gives_up_after_max_retries(self, client):
        client._http.request.return_value = response(429, {"code": "rate_limited", "message": "slow down"})
        with pytest.raises(NotionAPIError) as exc:
            client.get_block("abc")
        assert client._http.request.call_count == 5
        assert exc.value.status == 429

    def test_other_errors_are_not_retried(self, client):
        client._http.request.return_value = response(404, {"code": "object_not_found", "message": "nope"})
        with pytest.raises(NotionAPIError):
            client.get_block("abc")
        assert client._http.request.call_count == 1

    def test_token_required(self, monkeypatch):
        monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
        with pytest.raises(ValueError):
            NotionClient()


class TestTitles:
    def test_plain_page(self):
        assert page_title({"properties": {"title": {"type": "title", "title": [{"plain_text": "Home"}]}}}) == "Home"

    def test_database_row_with_named_title_column(self):
        page = {"properties": {
            "Status": {"type": "status", "status": {"name": "Done"}},
            "Task name": {"type": "title", "title": [{"plain_text": "Call "}, {"plain_text": "Boris"}]},
        }}
        assert page_title(page) == "Call Boris"

    def test_missing_title(self):
        assert page_title({}) == ""


class TestRichTextPassthrough:
    def test_mentions_and_equations_survive(self):
        rich = [
            {"type": "mention", "mention": {"type": "date", "date": {"start": "2026-12-01"}}, "plain_text": "2026-12-01", "annotations": {"bold": True}},
            {"type": "mention", "mention": {"type": "user", "user": {"object": "user", "id": "u1", "name": "Ann"}}, "plain_text": "@Ann"},
            {"type": "mention", "mention": {"type": "page", "page": {"id": "p1"}}, "plain_text": "Roadmap"},
            {"type": "equation", "equation": {"expression": "x^2"}, "plain_text": "x^2"},
        ]
        out = NotionClient.convert_rich_text_for_write(rich)
        assert [o["type"] for o in out] == ["mention", "mention", "mention", "equation"]
        assert out[0]["mention"] == {"type": "date", "date": {"start": "2026-12-01"}}
        assert out[0]["annotations"] == {"bold": True}
        assert out[1]["mention"] == {"type": "user", "user": {"id": "u1"}}
        assert out[3]["equation"] == {"expression": "x^2"}

    def test_unwritable_mention_falls_back_to_linked_text(self):
        rich = [{"type": "mention", "mention": {"type": "link_preview", "link_preview": {"url": "https://a.b"}}, "plain_text": "a.b", "href": "https://a.b"}]
        out = NotionClient.convert_rich_text_for_write(rich)
        assert out == [{"type": "text", "text": {"content": "a.b", "link": {"url": "https://a.b"}}}]

    def test_text_link_kept(self):
        out = NotionClient.convert_rich_text_for_write([text("go", text={"content": "go", "link": {"url": "https://a.b"}})])
        assert out[0]["text"]["link"] == {"url": "https://a.b"}

    def test_find_replace_keeps_mention(self, client):
        block = {"id": "b", "type": "paragraph", "paragraph": {"rich_text": [
            text("Due ALPHA on "),
            {"type": "mention", "mention": {"type": "date", "date": {"start": "2026-12-01"}}, "plain_text": "2026-12-01"},
        ]}}
        client.get_block = MagicMock(return_value=block)
        client.update_block = MagicMock(return_value={"id": "b"})
        result = client.find_and_replace_text("b", "ALPHA", "BETA")
        sent = client.update_block.call_args.args[1]["paragraph"]["rich_text"]
        assert result["replacements"] == 1
        assert sent[0]["text"]["content"] == "Due BETA on "
        assert sent[1]["type"] == "mention"

    def test_long_text_is_split(self):
        parts = NotionClient.make_rich_text_list("x" * 4500)
        assert [len(p["text"]["content"]) for p in parts] == [2000, 2000, 500]
        assert len(NotionClient.make_rich_text_list("")) == 1
        block = NotionClient.make_block("paragraph", "y" * 2001)
        assert len(block["paragraph"]["rich_text"]) == 2

    def test_update_block_text_splits_long_text(self, client):
        client.get_block = MagicMock(return_value={"type": "code", "code": {"language": "python"}})
        client.update_block = MagicMock(return_value={"id": "b"})
        client.update_block_text("b", "z" * 2001)
        sent = client.update_block.call_args.args[1]["code"]
        assert len(sent["rich_text"]) == 2 and sent["language"] == "python"

    def test_update_text_unsupported_type(self, client):
        client.get_block = MagicMock(return_value={"type": "image", "image": {}})
        with pytest.raises(ValueError):
            client.update_block_text("b", "x")
        with pytest.raises(ValueError):
            client.update_block_rich_text("b", [])


class TestConverters:
    @pytest.mark.parametrize("block,expected_type", [
        ({"type": "equation", "equation": {"expression": "E=mc^2"}}, "equation"),
        ({"type": "bookmark", "bookmark": {"url": "https://a.b", "caption": [text("c")]}}, "bookmark"),
        ({"type": "embed", "embed": {"url": "https://a.b"}}, "embed"),
        ({"type": "image", "image": {"type": "external", "external": {"url": "https://a.b/i.png"}, "caption": [text("c")]}}, "image"),
        ({"type": "pdf", "pdf": {"type": "external", "external": {"url": "https://a.b/f.pdf"}}}, "pdf"),
        ({"type": "code", "code": {"rich_text": [text("x")], "language": "python"}}, "code"),
        ({"type": "table_of_contents", "table_of_contents": {}}, "table_of_contents"),
        ({"type": "column_list", "column_list": {}, "children": [{"type": "column", "column": {}, "children": [para("x")]}]}, "column_list"),
        ({"type": "template", "template": {"rich_text": [text("x")]}}, "paragraph"),
    ])
    def test_supported(self, block, expected_type):
        assert NotionClient.convert_block_for_write(block)["type"] == expected_type

    @pytest.mark.parametrize("block", [
        {"type": "image", "image": {"type": "file", "file": {"url": "https://signed"}}},
        {"type": "child_page", "child_page": {"title": "Sub"}},
        {"type": "child_database", "child_database": {"title": "DB"}},
        {"type": "bookmark", "bookmark": {}},
        {"type": "synced_block", "synced_block": {}},
    ])
    def test_not_recreatable(self, block):
        assert NotionClient.convert_block_for_write(block) is None

    def test_toggleable_heading_and_todo_and_callout(self):
        h = NotionClient.convert_block_for_write({"type": "heading_2", "heading_2": {"rich_text": [text("h")], "is_toggleable": True}})
        assert h["heading_2"]["is_toggleable"] is True
        t = NotionClient.convert_block_for_write({"type": "to_do", "to_do": {"rich_text": [text("t")], "checked": True}})
        assert t["to_do"]["checked"] is True
        c = NotionClient.convert_block_for_write({"type": "callout", "callout": {"rich_text": [text("c")], "icon": {"type": "emoji", "emoji": "💡"}}})
        assert c["callout"]["icon"]["emoji"] == "💡"

    def test_unsupported_block_types_counts_nested(self):
        blocks = [
            para("ok", children=[{"type": "image", "image": {"type": "file", "file": {"url": "u"}}}]),
            {"type": "child_page", "child_page": {"title": "Sub"}},
            {"type": "equation", "equation": {"expression": "x"}},
        ]
        assert dict(NotionClient.unsupported_block_types(blocks)) == {"image": 1, "child_page": 1}

    def test_copy_converter_keeps_hosted_media_as_external(self):
        img = NotionClient._convert_block_for_copy({"type": "image", "image": {"type": "file", "file": {"url": "https://signed"}, "caption": [text("c")]}})
        assert img["image"]["external"]["url"] == "https://signed"
        f = NotionClient._convert_block_for_copy({"type": "file", "file": {"type": "file", "file": {"url": "https://signed"}, "name": "a.pdf", "caption": [text("c")]}})
        assert f["file"]["name"] == "a.pdf"
        assert NotionClient._convert_block_for_copy({"type": "image", "image": {"type": "file", "file": {}}}) is None
        assert NotionClient._convert_block_for_copy({"type": "file", "file": {"type": "external", "external": {}}}) is None
        assert NotionClient._convert_block_for_copy(para("x"))["type"] == "paragraph"

    def test_media_block_builders(self):
        assert NotionClient.make_image_block(file_upload_id="u", caption="c")["image"]["file_upload"] == {"id": "u"}
        assert NotionClient.make_image_block(external_url="https://a")["image"]["type"] == "external"
        assert NotionClient.make_file_block(external_url="https://a", caption="c", name="n")["file"]["name"] == "n"
        assert NotionClient.make_file_block(file_upload_id="u")["file"]["type"] == "file_upload"
        for maker in (NotionClient.make_image_block, NotionClient.make_file_block):
            with pytest.raises(ValueError):
                maker()
            with pytest.raises(ValueError):
                maker(file_upload_id="u", external_url="https://a")

    def test_make_todo_and_color(self):
        b = NotionClient.make_block("to_do", "x", checked=True, color="red")
        assert b["to_do"]["checked"] is True and b["to_do"]["color"] == "red"


class TestAppendTree:
    def test_deep_nesting_is_split_across_requests(self, client):
        fake = FakeNotion(client)
        block = NotionClient.convert_block_for_write(nested_bullets(1, 5))
        client.append_tree("page", [block])
        assert len(fake.requests) == 2  # levels 1-3, then 4-5
        chain, parent = [], "page"
        while fake.children.get(parent):
            node = fake.children[parent][0]
            chain.append(node["bulleted_list_item"]["rich_text"][0]["text"]["content"])
            parent = node["id"]
        assert chain == ["L1", "L2", "L3", "L4", "L5"]

    def test_large_table_rows_are_split(self, client):
        fake = FakeNotion(client)
        table = {"type": "table", "table": {"table_width": 1}, "children": [
            {"type": "table_row", "table_row": {"cells": [[text(str(i))]]}} for i in range(150)
        ]}
        created = client.append_tree("page", [NotionClient.convert_block_for_write(table)])
        assert len(fake.children[created[0]["id"]]) == 150
        assert [len(r[1]) for r in fake.requests] == [1, 50]

    def test_shallow_content_is_one_request(self, client):
        fake = FakeNotion(client)
        client.append_tree("page", [NotionClient.convert_block_for_write(nested_bullets(1, 3)), para("x")])
        assert len(fake.requests) == 1

    def test_column_list_inside_toggle_is_deferred(self, client):
        fake = FakeNotion(client)
        toggle = {"type": "toggle", "toggle": {"rich_text": [], "children": [
            {"type": "column_list", "column_list": {"children": [
                {"type": "column", "column": {"children": [para("in column")]}}]}}]}}
        client.append_tree("page", [toggle])
        assert len(fake.requests) == 2
        assert "children" not in fake.requests[0][1][0]["toggle"]


class TestRestore:
    SNAPSHOT = {"blocks": [para("restored 1"), para("restored 2"), {"type": "child_page", "child_page": {"title": "Sub"}}]}

    def test_writes_before_deleting_and_preserves_unrecreatable(self, client):
        existing = [
            para("old", "old1"),
            {"id": "sub", "type": "child_page", "child_page": {"title": "Sub"}},
            {"id": "db", "type": "child_database", "child_database": {"title": "DB"}},
            {"id": "img", "type": "image", "image": {"type": "file", "file": {"url": "u"}}},
            {"id": "tog", "type": "toggle", "toggle": {"rich_text": []}, "children": [
                {"id": "img2", "type": "image", "image": {"type": "file", "file": {"url": "u"}}}]},
        ]
        fake = FakeNotion(client, existing)
        result = client.restore_page_from_snapshot("page", self.SNAPSHOT)
        assert fake.deleted == ["old1"]
        assert result["created"] == 2 and result["deleted"] == 1
        assert result["preserved"] == {"child_page": 1, "child_database": 1, "image": 2}
        assert result["skipped"] == {"child_page": 1}
        assert {b["id"] for b in fake.children["page"]} >= {"sub", "db", "img", "tog"}
        assert fake.texts()[-2:] == ["restored 1", "restored 2"]

    def test_failed_write_rolls_back_and_keeps_old_content(self, client):
        many = {"blocks": [nested_bullets(1, 5)]}
        fake = FakeNotion(client, [para("old", "old1")], fail_on_call=2)
        with pytest.raises(NotionAPIError):
            client.restore_page_from_snapshot("page", many)
        assert [b["id"] for b in fake.children["page"]] == ["old1"]
        assert "old1" not in fake.deleted

    def test_reads_snapshot_file_and_expands_home(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        client.get_page = MagicMock(return_value={"id": "page"})
        client.get_block_children_deep = MagicMock(return_value=[para("héllo ✓", "b1")])
        snap = client.snapshot_page("page", "~/snaps/s.json")
        assert (tmp_path / "snaps" / "s.json").exists()
        assert snap["blocks"][0]["id"] == "b1"
        fake = FakeNotion(client)
        result = client.restore_page_from_snapshot("page", "~/snaps/s.json")
        assert result["created"] == 1 and fake.texts() == ["héllo ✓"]


class TestCopy:
    def _setup(self, client, blocks, fail=None):
        client.get_page = MagicMock(return_value={
            "icon": {"type": "emoji", "emoji": "🔬"},
            "properties": {"Task name": {"type": "title", "title": [{"plain_text": "Deal"}]}},
        })
        client.get_block_children_deep = MagicMock(return_value=blocks)
        client.create_page = MagicMock(return_value={"id": "new", "url": "u"})
        client.append_tree = MagicMock(side_effect=fail)
        client._patch = MagicMock()

    def test_full_copy_archives_original(self, client):
        self._setup(client, [para("a"), {"type": "equation", "equation": {"expression": "x"}}])
        out = client.copy_page_to_parent("src", "dst")
        assert out["blocks_copied"] == 2 and out["skipped"] == {} and out["original_archived"] is True
        assert client.create_page.call_args.kwargs["title"] == "Deal"
        assert client.create_page.call_args.kwargs["icon"] == "🔬"
        client._patch.assert_called_once_with("/pages/src", {"archived": True})

    def test_skipped_blocks_keep_original(self, client):
        self._setup(client, [para("a"), {"type": "child_database", "child_database": {"title": "DB"}}])
        out = client.copy_page_to_parent("src", "dst")
        assert out["skipped"] == {"child_database": 1}
        assert out["original_archived"] is False and "warning" in out
        client._patch.assert_not_called()

    def test_archive_original_false(self, client):
        self._setup(client, [para("a")])
        out = client.copy_page_to_parent("src", "dst", archive_original=False)
        assert out["original_archived"] is False and "warning" not in out

    def test_refuses_nested_subpages(self, client):
        self._setup(client, [para("a", children=[{"type": "child_page", "child_page": {"title": "S"}}])])
        with pytest.raises(ValueError):
            client.copy_page_to_parent("src", "dst")
        client.create_page.assert_not_called()

    def test_failed_write_trashes_partial_copy_only(self, client):
        self._setup(client, [para("a")], fail=NotionAPIError(400, "validation_error", "boom", "PATCH", "/x"))
        with pytest.raises(NotionAPIError):
            client.copy_page_to_parent("src", "dst")
        client._patch.assert_called_once_with("/pages/new", {"archived": True})


class TestPagesAndBlocks:
    def test_move_page(self, client):
        client._post = MagicMock(return_value={"id": "p"})
        client.move_page("p", "parent")
        client._post.assert_called_once_with("/pages/p/move", {"parent": {"type": "page_id", "page_id": "parent"}})
        with pytest.raises(ValueError):
            client.move_page("p", "db", parent_type="database_id")

    def test_create_and_update_page_bodies(self, client):
        client._post = MagicMock(return_value={"id": "p"})
        client.create_page("par", "T", parent_type="database_id", children=[para("x")], icon="🧪", cover_url="https://c", extra_properties={"Status": {"status": {"name": "Done"}}})
        body = client._post.call_args.args[1]
        assert body["parent"] == {"database_id": "par"} and "Status" in body["properties"]
        assert body["icon"]["emoji"] == "🧪" and body["cover"]["external"]["url"] == "https://c"
        client._patch = MagicMock(return_value={"id": "p"})
        client.update_page("p", properties={"a": 1}, icon="x", cover_url="https://c", archived=True)
        assert set(client._patch.call_args.args[1]) == {"properties", "icon", "cover", "archived"}
        client.restore_page("p")
        assert client._patch.call_args.args == ("/pages/p", {"archived": False})

    def test_pagination(self, client):
        pages = [
            {"results": [{"id": 1}], "has_more": True, "next_cursor": "c1"},
            {"results": [{"id": 2}], "has_more": False},
        ]
        for method in (lambda: client.get_block_children("b"), lambda: client.list_comments("b"), lambda: client.list_users()):
            client._get = MagicMock(side_effect=[dict(p) for p in pages])
            assert [x["id"] for x in method()] == [1, 2]
            assert client._get.call_args.args[1]["start_cursor"] == "c1"

    def test_deep_read_skips_subpages(self, client):
        tree = {
            "root": [{"id": "t", "type": "toggle", "has_children": True}, {"id": "sub", "type": "child_page", "has_children": True}],
            "t": [{"id": "x", "type": "paragraph", "has_children": False}],
        }
        client.get_block_children = MagicMock(side_effect=lambda bid: [dict(b) for b in tree[bid]])
        out = client.get_block_children_deep("root")
        assert out[0]["children"][0]["id"] == "x" and "children" not in out[1]
        assert client.get_block_children_deep("root", max_depth=0) == []

    def test_query_truncation_flag(self, client):
        client._post = MagicMock(return_value={"results": [{}] * 100, "has_more": True, "next_cursor": "c"})
        rows, truncated = client.query_database_page("db", filter={"a": 1}, sorts=[{"b": 2}])
        assert len(rows) == 500 and truncated is True and client._post.call_count == 5
        client._post = MagicMock(return_value={"results": [{}] * 3, "has_more": False})
        assert client.query_database_page("db") == ([{}] * 3, False)
        assert len(client.query_database("db")) == 3

    def test_search_clamps_page_size(self, client):
        client._post = MagicMock(return_value={"results": []})
        client.search("q", filter_type="database", page_size=500)
        assert client._post.call_args.args[1]["page_size"] == 100
        assert client._post.call_args.args[1]["filter"]["value"] == "database"
        client.search_pages("q", page_size=0)
        assert client._post.call_args.args[1]["page_size"] == 1

    def test_append_children_batches(self, client):
        client._patch = MagicMock(side_effect=lambda path, body: {"results": [{"id": i} for i in range(len(body["children"]))]})
        created = client.append_children("p", [para(str(i)) for i in range(230)])
        assert len(created) == 230
        assert [len(c.args[1]["children"]) for c in client._patch.call_args_list] == [100, 100, 30]

    def test_insert_after_and_prepend(self, client):
        client.get_block = MagicMock(return_value={"parent": {"page_id": "par"}})
        client._patch = MagicMock(return_value={"results": [{"id": "n"}]})
        client.insert_blocks_after("b", [para("x")])
        assert client._patch.call_args.args == ("/blocks/par/children", {"children": [para("x")], "after": "b"})
        client.prepend_children("par", [para("x")])
        assert client._patch.call_args.args[1]["position"] == {"type": "start"}
        client.get_block = MagicMock(return_value={"parent": {"workspace": True}})
        with pytest.raises(ValueError):
            client.insert_blocks_after("b", [para("x")])

    def test_simple_passthroughs(self, client):
        client._get = MagicMock(return_value={"id": "x"}); client._post = MagicMock(return_value={"id": "x"}); client._delete = MagicMock(return_value={})
        client.get_page("p"); client.get_database("d"); client.get_self(); client.get_file_upload("u")
        assert [c.args[0] for c in client._get.call_args_list] == ["/pages/p", "/databases/d", "/users/me", "/file_uploads/u"]
        client.create_comment("p", "hi")
        assert client._post.call_args.args[0] == "/comments"
        client.delete_block("b")
        client._delete.assert_called_once_with("/blocks/b")
        client.close()


class TestTables:
    def test_insert_row_variants(self, client):
        client.get_block = MagicMock(return_value={"type": "table", "table": {"table_width": 3}})
        client.get_block_children = MagicMock(return_value=[{"id": "r1"}, {"id": "r2"}])
        client.insert_blocks_after = MagicMock(return_value=[{"id": "new"}])
        assert client.insert_table_row_after("t", ["a", ["b", {"type": "text", "text": {"content": "c"}}], 3]) == {"id": "new"}
        anchor, rows = client.insert_blocks_after.call_args.args
        assert anchor == "r2"
        cells = rows[0]["table_row"]["cells"]
        assert cells[0][0]["text"]["content"] == "a" and len(cells[1]) == 2 and cells[2][0]["text"]["content"] == "3"
        client.insert_table_row_after("t", ["a", "b", "c"], after_row_id="r1")
        assert client.insert_blocks_after.call_args.args[0] == "r1"

    def test_insert_row_into_empty_table_and_validation(self, client):
        client.get_block = MagicMock(return_value={"type": "table", "table": {"table_width": 1}})
        client.get_block_children = MagicMock(return_value=[])
        client.append_children = MagicMock(return_value=[{"id": "new"}])
        assert client.insert_table_row_after("t", ["a"]) == {"id": "new"}
        with pytest.raises(ValueError):
            client.insert_table_row_after("t", ["a", "b"])
        client.get_block = MagicMock(return_value={"type": "paragraph"})
        with pytest.raises(ValueError):
            client.insert_table_row_after("t", ["a"])

    def test_update_cell(self, client):
        client.get_block = MagicMock(return_value={"type": "table_row", "table_row": {"cells": [[text("a")], [text("b")]]}})
        client.update_block = MagicMock(return_value={"id": "r"})
        client.update_table_cell("r", 1, "B")
        cells = client.update_block.call_args.args[1]["table_row"]["cells"]
        assert cells[1] == [{"type": "text", "text": {"content": "B"}}] and cells[0][0]["plain_text"] == "a"
        with pytest.raises(ValueError):
            client.update_table_cell("r", 5, "x")
        client.get_block = MagicMock(return_value={"type": "paragraph"})
        with pytest.raises(ValueError):
            client.update_table_cell("r", 0, "x")


class TestFiles:
    def test_file_root_boundary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTION_MCP_FILE_ROOT", str(tmp_path))
        assert resolve_local_path(str(tmp_path / "a" / "b.png")) == tmp_path / "a" / "b.png"
        with pytest.raises(ValueError):
            resolve_local_path("/etc/passwd")
        with pytest.raises(ValueError):
            resolve_local_path(str(tmp_path / ".." / "escape.txt"))
        monkeypatch.delenv("NOTION_MCP_FILE_ROOT")
        assert resolve_local_path("/etc/passwd").as_posix() == "/etc/passwd"

    def test_upload_flow(self, client, tmp_path, monkeypatch):
        f = tmp_path / "a.png"; f.write_bytes(b"png")
        client._post = MagicMock(return_value={"id": "up1"})
        client._get = MagicMock(return_value={"id": "up1", "status": "uploaded"})
        posted = MagicMock(return_value=response(200, {"id": "up1"}))
        monkeypatch.setattr("notion_mcp.client.httpx.post", posted)
        assert client.upload_file(str(f))["status"] == "uploaded"
        assert client._post.call_args.args[1] == {"mode": "single_part", "filename": "a.png", "content_type": "image/png"}
        assert posted.call_args.args[0].endswith("/file_uploads/up1/send")
        posted.return_value = response(400, {"code": "validation_error", "message": "too big"})
        with pytest.raises(NotionAPIError, match="too big"):
            client.send_file_upload("up1", str(f))
        with pytest.raises(FileNotFoundError):
            client.upload_file(str(tmp_path / "missing.png"))
        with pytest.raises(FileNotFoundError):
            client.send_file_upload("up1", str(tmp_path / "missing.png"))

    def test_download(self, client, tmp_path, monkeypatch):
        import httpx
        monkeypatch.setattr("notion_mcp.client.httpx.get", MagicMock(return_value=httpx.Response(200, content=b"data", request=httpx.Request("GET", "https://x"))))
        out = client.download_file_from_url("https://x/y.png", str(tmp_path / "d" / "y.png"))
        assert out["size"] == 4 and (tmp_path / "d" / "y.png").read_bytes() == b"data"

    def test_image_url_from_block(self, client):
        client.get_block = MagicMock(return_value={"type": "image", "image": {"type": "external", "external": {"url": "https://e"}}})
        assert client.get_image_url_from_block("b") == "https://e"
        client.get_block = MagicMock(return_value={"type": "image", "image": {"type": "file", "file": {"url": "https://s"}}})
        assert client.get_image_url_from_block("b") == "https://s"
        client.get_block = MagicMock(return_value={"type": "paragraph"})
        with pytest.raises(ValueError):
            client.get_image_url_from_block("b")


class TestMarkdown:
    def test_all_rendered_types(self):
        blocks = [
            {"type": "heading_2", "heading_2": {"rich_text": [text("H2")]}},
            {"type": "heading_3", "heading_3": {"rich_text": [text("H3")]}},
            {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [text("one")]}},
            {"type": "to_do", "to_do": {"rich_text": [text("task")], "checked": True}},
            {"type": "quote", "quote": {"rich_text": [text("q")]}},
            {"type": "callout", "callout": {"rich_text": [text("c")]}},
            {"type": "code", "code": {"rich_text": [text("a=1")], "language": "python"}},
            {"type": "divider", "divider": {}},
            {"type": "toggle", "toggle": {"rich_text": [text("tg")]}, "children": [para("inside")]},
            {"type": "table", "table": {}, "children": [{"type": "table_row", "table_row": {"cells": [[text("a")], [text("b")]]}}]},
            {"type": "child_page", "child_page": {"title": "Sub"}},
            {"type": "child_database", "child_database": {"title": "Tasks"}},
            {"type": "equation", "equation": {"expression": "E=mc^2"}},
            {"type": "bookmark", "bookmark": {"url": "https://a.b"}},
            {"type": "image", "image": {"type": "external", "external": {"url": "https://i"}, "caption": [text("cap")]}},
            {"type": "pdf", "pdf": {"type": "file", "file": {"url": "https://f"}}},
            {"type": "synced_block", "synced_block": {}},
            {"type": "template", "template": {"rich_text": [text("tpl")]}},
        ]
        md = NotionClient.blocks_to_markdown(blocks)
        for expected in ("## H2", "### H3", "1. one", "- [x] task", "> q", "> **Note:** c", "```python", "---", "<toggle> tg", "  inside", "[table]", "  | a | b |", "[page: Sub]", "[database: Tasks]", "$$ E=mc^2 $$", "[bookmark](https://a.b)", "![cap](https://i)", "[pdf](https://f)", "tpl"):
            assert expected in md, expected
