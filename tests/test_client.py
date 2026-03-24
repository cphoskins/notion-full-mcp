"""Tests for NotionClient — mocked, no real credentials needed."""

import json
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
