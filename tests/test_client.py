"""Tests for NotionClient — mocked, no real credentials needed."""

from unittest.mock import MagicMock, patch

import pytest

from notion_mcp.client import NotionClient


@pytest.fixture
def client():
    with patch("notion_mcp.client.httpx.Client"):
        return NotionClient(token="test-token-123")


class TestRichTextConversion:
    def test_make_rich_text_plain(self):
        rt = NotionClient.make_rich_text("hello")
        assert rt["text"]["content"] == "hello"
        assert rt["annotations"]["bold"] is False

    def test_make_rich_text_bold(self):
        rt = NotionClient.make_rich_text("bold", bold=True)
        assert rt["annotations"]["bold"] is True

    def test_make_rich_text_with_link(self):
        rt = NotionClient.make_rich_text("click", link="https://example.com")
        assert rt["text"]["link"]["url"] == "https://example.com"

    def test_convert_rich_text_for_write(self):
        read_format = [
            {
                "type": "text",
                "text": {"content": "hello", "link": None},
                "annotations": {
                    "bold": True,
                    "italic": False,
                    "strikethrough": False,
                    "underline": False,
                    "code": False,
                    "color": "default",
                },
                "plain_text": "hello",
            }
        ]
        result = NotionClient.convert_rich_text_for_write(read_format)
        assert len(result) == 1
        assert result[0]["text"]["content"] == "hello"
        assert result[0]["annotations"]["bold"] is True


class TestBlockConversion:
    def test_convert_paragraph(self):
        block = {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"plain_text": "test", "annotations": {}}],
                "color": "default",
            },
        }
        result = NotionClient.convert_block_for_write(block)
        assert result["type"] == "paragraph"
        assert result["paragraph"]["rich_text"][0]["text"]["content"] == "test"

    def test_convert_heading(self):
        block = {
            "type": "heading_1",
            "heading_1": {
                "rich_text": [{"plain_text": "Title", "annotations": {}}],
                "color": "default",
            },
        }
        result = NotionClient.convert_block_for_write(block)
        assert result["type"] == "heading_1"

    def test_convert_divider(self):
        block = {"type": "divider", "divider": {}}
        result = NotionClient.convert_block_for_write(block)
        assert result["type"] == "divider"

    def test_convert_table_with_rows(self):
        block = {
            "type": "table",
            "table": {
                "table_width": 2,
                "has_column_header": True,
                "has_row_header": False,
            },
            "children": [
                {
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [{"plain_text": "A", "annotations": {}}],
                            [{"plain_text": "B", "annotations": {}}],
                        ]
                    },
                }
            ],
        }
        result = NotionClient.convert_block_for_write(block)
        assert result["type"] == "table"
        assert len(result["table"]["children"]) == 1
        assert result["table"]["children"][0]["type"] == "table_row"

    def test_convert_bulleted_list_with_children(self):
        block = {
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"plain_text": "parent", "annotations": {}}],
            },
            "children": [
                {
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"plain_text": "child", "annotations": {}}],
                    },
                }
            ],
        }
        result = NotionClient.convert_block_for_write(block)
        assert result["type"] == "bulleted_list_item"
        children = result["bulleted_list_item"].get("children", [])
        assert len(children) == 1

    def test_convert_unsupported_returns_none(self):
        block = {"type": "unsupported", "unsupported": {}}
        result = NotionClient.convert_block_for_write(block)
        assert result is None


class TestMakeBlock:
    def test_make_paragraph(self):
        block = NotionClient.make_block("paragraph", "hello world")
        assert block["type"] == "paragraph"
        assert block["paragraph"]["rich_text"][0]["text"]["content"] == "hello world"

    def test_make_heading(self):
        block = NotionClient.make_block("heading_2", "Section Title")
        assert block["type"] == "heading_2"

    def test_make_divider(self):
        block = NotionClient.make_block("divider")
        assert block["type"] == "divider"

    def test_make_code(self):
        block = NotionClient.make_block("code", "print('hi')", language="python")
        assert block["code"]["language"] == "python"

    def test_make_unsupported_raises(self):
        with pytest.raises(ValueError):
            NotionClient.make_block("table", "nope")


class TestFindAndReplaceText:
    """Regression tests for the find/replace silent-no-op bug.

    Previously, find_and_replace_text would silently report success when
    old_text did not match any content in the block (including Unicode
    mismatches like en-dash U+2013 vs hyphen U+002D), so edits appeared to
    land when nothing had changed. Now zero replacements raise ValueError with a diagnostic
    preview of the block's current text so the caller can see exactly
    what characters are in the block and pick the right fix tool.
    """

    def _make_paragraph_block(self, text: str) -> dict:
        return {
            "id": "test-block-id",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": text, "link": None},
                        "plain_text": text,
                        "annotations": {
                            "bold": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False,
                            "code": False,
                            "color": "default",
                        },
                    }
                ],
            },
        }

    def test_successful_replacement_returns_count(self, client):
        block = self._make_paragraph_block("The total is 256 this quarter.")
        client.get_block = MagicMock(return_value=block)
        client.update_block_rich_text = MagicMock(return_value={"id": "test-block-id"})

        result = client.find_and_replace_text(
            "test-block-id", "256", "~450"
        )

        assert result["id"] == "test-block-id"
        assert result["replacements"] == 1

    def test_multiple_replacements_counted(self, client):
        block = self._make_paragraph_block("256 items today. The 256 total includes 256 more.")
        client.get_block = MagicMock(return_value=block)
        client.update_block_rich_text = MagicMock(return_value={"id": "test-block-id"})

        result = client.find_and_replace_text(
            "test-block-id", "256", "~450"
        )

        assert result["replacements"] == 3

    def test_no_match_raises_valueerror_with_preview(self, client):
        block = self._make_paragraph_block("The current text uses en-dash U+2013.")
        client.get_block = MagicMock(return_value=block)

        with pytest.raises(ValueError) as exc_info:
            client.find_and_replace_text(
                "test-block-id", "NOT IN BLOCK", "replacement"
            )

        error_msg = str(exc_info.value)
        assert "test-block-id" in error_msg
        assert "NOT IN BLOCK" in error_msg
        assert "en-dash" in error_msg  # preview includes actual content
        assert "Unicode character mismatch" in error_msg  # actionable hint

    def test_en_dash_hyphen_mismatch_raises(self, client):
        """En-dash in the block, hyphen-minus in the search string."""
        # Block contains en-dash U+2013
        block = self._make_paragraph_block("Allow 10\u201320 working days.")
        client.get_block = MagicMock(return_value=block)

        # Caller searches for hyphen-minus U+002D (different character)
        with pytest.raises(ValueError) as exc_info:
            client.find_and_replace_text(
                "test-block-id", "10-20", "15-30"
            )

        # Error should include the actual block content so caller can see
        # what characters are really in there
        assert "working days" in str(exc_info.value)

    def test_empty_old_text_raises(self, client):
        """Empty old_text would match infinitely many positions; treat as no-match."""
        block = self._make_paragraph_block("Some content.")
        client.get_block = MagicMock(return_value=block)

        with pytest.raises(ValueError):
            client.find_and_replace_text("test-block-id", "", "replacement")

    def test_table_row_successful_replacement(self, client):
        block = {
            "id": "test-row-id",
            "type": "table_row",
            "table_row": {
                "cells": [
                    [{"type": "text", "text": {"content": "Enterprise", "link": None},
                      "plain_text": "Enterprise", "annotations": {}}],
                    [{"type": "text", "text": {"content": "10,000+ employees", "link": None},
                      "plain_text": "10,000+ employees", "annotations": {}}],
                ],
            },
        }
        client.get_block = MagicMock(return_value=block)
        client.update_block = MagicMock(return_value={"id": "test-row-id"})

        result = client.find_and_replace_text(
            "test-row-id", "10,000+ employees", "5,000+ employees"
        )

        assert result["replacements"] == 1

    def test_table_row_no_match_raises(self, client):
        block = {
            "id": "test-row-id",
            "type": "table_row",
            "table_row": {
                "cells": [
                    [{"type": "text", "text": {"content": "Enterprise", "link": None},
                      "plain_text": "Enterprise", "annotations": {}}],
                    [{"type": "text", "text": {"content": "5,000+ employees", "link": None},
                      "plain_text": "5,000+ employees", "annotations": {}}],
                ],
            },
        }
        client.get_block = MagicMock(return_value=block)

        with pytest.raises(ValueError) as exc_info:
            client.find_and_replace_text(
                "test-row-id", "10,000+ employees", "5,000+ employees"
            )

        assert "table_row" in str(exc_info.value)


class TestBlocksToMarkdown:
    def test_basic_blocks(self):
        blocks = [
            {
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [{"plain_text": "Title"}],
                },
            },
            {
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"plain_text": "Body text"}],
                },
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"plain_text": "Item 1"}],
                },
            },
        ]
        md = NotionClient.blocks_to_markdown(blocks)
        assert "# Title" in md
        assert "Body text" in md
        assert "- Item 1" in md
