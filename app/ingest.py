"""Corpus import: parse a document, version it, index it.

Only the formats the demo corpus actually uses are supported. An unsupported
suffix is an explicit failure, never a silent skip: a document that was never
indexed must not look like one that was.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.knowledge import KnowledgeIndex

__all__ = ["UnsupportedDocument", "load_document", "import_corpus"]

_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


class UnsupportedDocument(ValueError):
    """The file type has no parser in this build."""


def load_document(path: Path) -> str:
    """Return the document's text, or raise ``UnsupportedDocument``."""
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8-sig")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    raise UnsupportedDocument(f"unsupported document type: {suffix or '<none>'}")


def _revision(text: str) -> str:
    """A content-derived revision so a changed file is a new revision."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def import_corpus(
    *,
    data_dir: Path,
    corpus: str,
    scope: str,
    source_dir: Path,
) -> dict:
    """Index every supported file under ``source_dir`` into one corpus.

    Re-importing the same file replaces its previous chunks (the revision is
    content-derived). Failures are returned, not raised: one bad file must not
    abort the rest of the import.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    index = await KnowledgeIndex.open(
        data_dir / "corpus.db", corpus=corpus, access_scope=scope
    )
    documents = 0
    failures: list[str] = []
    try:
        for path in sorted(Path(source_dir).rglob("*")):
            if not path.is_file():
                continue
            try:
                text = load_document(path)
            except UnsupportedDocument as exc:
                failures.append(f"{path.name}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one file cannot abort the import
                failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
                continue
            if not text.strip():
                failures.append(f"{path.name}: no extractable text")
                continue
            try:
                await index.add_document(
                    source_uri=path.name,
                    source_revision=_revision(text),
                    text=text,
                )
                documents += 1
            except Exception as exc:  # noqa: BLE001 - reported per file
                failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
        chunks = await index.chunk_count()
    finally:
        await index.close()
    return {"documents": documents, "chunks": chunks, "failures": failures}
