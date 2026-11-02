"""Extract plain text from uploaded PDFs, Word (.docx) and PowerPoint (.pptx) files."""
import re
import zipfile
from xml.etree import ElementTree

CHUNK_CHARS = 1000
OVERLAP = 150
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


MAX_PDF_PAGES = 300          # very long PDFs: only the first pages are read (keeps uploads fast)
XML_LIMIT = 30 * 1024 * 1024  # biggest Word/PowerPoint XML part we'll read


class UnsupportedFile(Exception):
    pass


def _xml(archive, name):
    """Parse one XML part safely: size-limited (zip bombs) and no DOCTYPE/entities (entity bombs)."""
    from apps.core.uploads import read_zip_entry

    data = read_zip_entry(archive, name, XML_LIMIT)
    head = data[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data.upper():
        raise UnsupportedFile("This file couldn't be read.")
    return ElementTree.fromstring(data)


def _clean(text):
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def pages_from_file(fileobj, filename):
    """Return [(page_number_or_None, text), ...]."""
    name = filename.lower()
    fileobj.seek(0)
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(fileobj)
        pages = []
        for i, page in enumerate(reader.pages):
            if i >= MAX_PDF_PAGES:
                break
            pages.append((i + 1, _clean(page.extract_text() or "")))
        return pages
    if name.endswith(".docx"):
        from apps.core.uploads import check_archive

        with zipfile.ZipFile(fileobj) as z:
            check_archive(z)
            root = _xml(z, "word/document.xml")
        paragraphs = ["".join(t.text or "" for t in p.iter(f"{W_NS}t")) for p in root.iter(f"{W_NS}p")]
        return [(None, _clean("\n".join(p for p in paragraphs if p.strip())))]
    if name.endswith(".pptx"):
        from apps.core.uploads import check_archive

        with zipfile.ZipFile(fileobj) as z:
            check_archive(z)
            slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
            out = []
            for i, slide in enumerate(slides):
                root = _xml(z, slide)
                out.append((i + 1, _clean("\n".join(t.text or "" for t in root.iter(f"{A_NS}t")))))
            return out
    raise UnsupportedFile("NexAI can read PDF, .docx and .pptx files. Older .doc and .ppt files can't be read.")


def chunk(pages):
    """Split page texts into overlapping passages of about CHUNK_CHARS characters."""
    chunks = []
    for page, text in pages:
        start = 0
        while start < len(text):
            end = min(len(text), start + CHUNK_CHARS)
            if end < len(text):  # end on a sentence or word boundary when possible
                cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
                if cut > start + CHUNK_CHARS // 2:
                    end = cut + 1
            piece = text[start:end].strip()
            if len(piece) > 30:
                chunks.append((page, piece))
            if end >= len(text):
                break
            start = max(end - OVERLAP, start + 1)
    return chunks
