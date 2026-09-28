"""Deterministic character chunking shared by preview and durable processing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ChunkSettings:
    mode: str = "general"
    parentMode: str = "paragraph"
    separator: str = "\n\n"
    maxLength: int = 1024
    overlap: int = 50
    childSeparator: str = "\n"
    childMaxLength: int = 256
    replaceWhitespace: bool = False
    removeUrls: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"general", "parent_child"} or self.parentMode not in {
            "paragraph",
            "full_doc",
        }:
            raise ValueError("invalid chunk mode")
        if not 1 <= self.maxLength <= 32768 or not 0 <= self.overlap < self.maxLength:
            raise ValueError("overlap must be smaller than maximum length (1–32768)")
        if not 1 <= self.childMaxLength <= 32768:
            raise ValueError("child maximum length must be between 1 and 32768")
        if len(self.separator) > 100 or len(self.childSeparator) > 100:
            raise ValueError("separator is too long")


def clean_text(text: str, settings: ChunkSettings) -> str:
    if settings.removeUrls:
        text = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "", text)
    if settings.replaceWhitespace:
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, settings: ChunkSettings, *, child: bool = False) -> list[str]:
    text = clean_text(text, settings)
    maximum = settings.childMaxLength if child else settings.maxLength
    separator = settings.childSeparator if child else settings.separator
    overlap = 0 if child else settings.overlap
    result: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + maximum, len(text))
        if separator and end < len(text):
            boundary = text.rfind(separator, start + overlap + 1, end)
            if boundary >= 0:
                end = boundary + len(separator)
        value = text[start:end].strip()
        if value:
            result.append(value)
            if len(result) > 10000:
                raise ValueError("document has too many chunks")
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return result


def new_chunk(content: str, position: int, *, edited: bool = False) -> dict[str, Any]:
    if not content.strip() or len(content) > 1_000_000 or "\0" in content:
        raise ValueError("chunk content must be nonempty safe text of at most 1000000 characters")
    return dict(
        chunkId="chunk_" + uuid4().hex,
        content=content,
        enabled=True,
        edited=edited,
        position=position,
        charCount=len(content),
        tokens=0,
        keywords=[],
        summary=None,
        children=[],
        indexStatus="pending",
        indexError=None,
        version=1,
    )


def generate_chunks(text: str, settings: ChunkSettings) -> list[dict[str, Any]]:
    parents = (
        [clean_text(text, settings)]
        if settings.mode == "parent_child" and settings.parentMode == "full_doc"
        else split_text(text, settings)
    )
    result: list[dict[str, Any]] = []
    for content in parents:
        if not content:
            continue
        if len(content) > 32768:
            raise ValueError("full-document parent exceeds 32768 characters; use paragraph parents")
        chunk = new_chunk(content, len(result) + 1)
        if settings.mode == "parent_child":
            chunk["children"] = [
                new_chunk(value, index + 1)
                for index, value in enumerate(split_text(content, settings, child=True))
            ]
        result.append(chunk)
        if sum(1 + len(item["children"]) for item in result) > 10000:
            raise ValueError("document has too many chunks")
    if sum(1 + len(item["children"]) for item in result) > 10000:
        raise ValueError("document has too many chunks")
    return result
