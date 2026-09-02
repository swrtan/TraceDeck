"""Small, conservative policy for retaining human prompts locally."""

from __future__ import annotations

import re
from pathlib import PurePath


_DOCUMENT_EXTENSIONS = {".doc", ".docx", ".pdf", ".odt", ".rtf", ".txt", ".md", ".csv", ".xls", ".xlsx", ".ppt", ".pptx"}
_LABELLED_DOCUMENT = re.compile(r"^(?:file|document|attachment|attached file)\s*:\s*(.+)$", re.IGNORECASE)
_AMBIENT_BROWSER_CONTEXT = re.compile(r"<in-app-browser-context\b[^>]*>.*?</in-app-browser-context>", re.IGNORECASE | re.DOTALL)
_REQUEST_HEADING = re.compile(r"^\s*##\s*My request\s*:\s*", re.IGNORECASE)
_ATTACHMENT_SECTION = re.compile(r"^\s*#\s*Files\s+(?:mentioned|pasted)\s+by\s+the\s+user\s*:\s*.*?(?=^\s*##\s*My request\s*:|\Z)", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_ATTACHMENT_ENTRY = re.compile(r"^\s*##\s*[\"'“”]?[^\r\n:]+[\"'“”]?\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_PASTED_CODEX_CONTEXT = re.compile(
    r"(?:The following is the Codex agent history\b.*?(?:approval assessment|request action you are assessing)|"
    r"Codex agent history\b.*?TRANSCRIPT START|APPROVAL REQUEST START)",
    re.IGNORECASE | re.DOTALL,
)


def _attachment_names(value: str) -> list[str]:
    section = _ATTACHMENT_SECTION.search(value)
    if not section:
        return []
    names: list[str] = []
    for match in _ATTACHMENT_ENTRY.finditer(section.group(0)):
        name = PurePath(match.group(1).strip().strip('"\'`').replace("\\", "/")).name
        if name and name not in names and len(name) <= 240:
            names.append(name)
    return names


def _remove_transport_wrappers(value: str) -> tuple[str, list[str]]:
    names = _attachment_names(value)
    cleaned = _AMBIENT_BROWSER_CONTEXT.sub("", value).strip()
    cleaned = _ATTACHMENT_SECTION.sub("", cleaned).strip()
    request = _REQUEST_HEADING.search(cleaned)
    if request:
        cleaned = cleaned[request.end():].strip()
    return cleaned, names


def _document_name(value: str) -> str | None:
    candidate = value.strip().strip('"\'`')
    if candidate.startswith("@"):
        candidate = candidate[1:].strip()
    name = PurePath(candidate.replace("\\", "/")).name
    suffix = PurePath(name).suffix.lower()
    if name and suffix in _DOCUMENT_EXTENSIONS and len(name) <= 240:
        return name
    return None


def prompt_for_storage(value: object) -> str | None:
    """Keep human text; replace explicit document references with their name."""

    if value is None:
        return None
    text, attachments = _remove_transport_wrappers(str(value))
    if not text:
        return None
    if _PASTED_CODEX_CONTEXT.search(text):
        return None
    labelled = _LABELLED_DOCUMENT.match(text)
    name = _document_name(labelled.group(1)) if labelled else _document_name(text)
    if name:
        text = f"[Document: {name}]"
    if attachments:
        text = f"[Attached: {', '.join(attachments)}]\n{text}"
    return text
