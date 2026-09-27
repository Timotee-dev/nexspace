"""Extract plain text from uploaded PDFs, Word (.docx) and PowerPoint (.pptx) files."""
import re
import zipfile
from xml.etree import ElementTree

CHUNK_CHARS = 1000
OVERLAP = 150
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class UnsupportedFile(Exception):
    pass


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
        return [(i + 1, _clean(page.extract_text() or "")) for i, page in enumerate(reader.pages)]
    if name.endswith(".docx"):
        with zipfile.ZipFile(fileobj) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
        paragraphs = ["".join(t.text or "" for t in p.iter(f"{W_NS}t")) for p in root.iter(f"{W_NS}p")]
        return [(None, _clean("\n".join(p for p in paragraphs if p.strip())))]
    if name.endswith(".pptx"):
        with zipfile.ZipFile(fileobj) as z:
            slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
            out = []
            for i, slide in enumerate(slides):
                root = ElementTree.fromstring(z.read(slide))
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
