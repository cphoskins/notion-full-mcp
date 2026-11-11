"""Shared fixtures. All HTTP is mocked; no credentials are needed."""

from unittest.mock import patch

import httpx
import pytest

from notion_mcp.client import NotionClient


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("notion_mcp.client.time.sleep"):
        yield


@pytest.fixture
def client():
    with patch("notion_mcp.client.httpx.Client"):
        return NotionClient(token="test-token-123")


def response(status: int, body: dict | None = None, headers: dict | None = None):
    return httpx.Response(
        status,
        json=body or {},
        headers=headers or {},
        request=httpx.Request("GET", "https://api.notion.com/v1/x"),
    )


def text(content: str, **extra) -> dict:
    return {
        "type": "text",
        "text": {"content": content, "link": None},
        "plain_text": content,
        "annotations": {},
        **extra,
    }


def para(content: str, block_id: str = "", children: list | None = None) -> dict:
    block = {"type": "paragraph", "paragraph": {"rich_text": [text(content)]}}
    if block_id:
        block["id"] = block_id
    if children:
        block["children"] = children
        block["has_children"] = True
    return block
