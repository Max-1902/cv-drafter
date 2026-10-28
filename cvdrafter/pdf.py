"""DOCX -> PDF with Microsoft Word (macOS) or LibreOffice.

Word on macOS is sandboxed: opening a file outside its container pops up a hidden "Grant File Access" dialog
and the conversion hangs. So we copy the file into Word's own container, convert there, and move the PDF back.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

WORD_APP = Path("/Applications/Microsoft Word.app")
WORD_TMP = Path.home() / "Library/Containers/com.microsoft.Word/Data/tmp/cvdrafter"

SCRIPT = """with timeout of 90 seconds
tell application "Microsoft Word"
  set d to open file name "{src}"
  save as d file name "{dst}" file format format PDF
  close d saving no
end tell
end timeout"""


def _word(docx: Path, pdf: Path) -> bool:
    if not WORD_APP.exists():
        return False
    WORD_TMP.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    src, dst = WORD_TMP / f"{stem}.docx", WORD_TMP / f"{stem}.pdf"
    shutil.copy(docx, src)
    try:
        subprocess.run(["osascript", "-e", SCRIPT.format(src=src, dst=dst)], capture_output=True, timeout=120)
        if dst.exists():
            shutil.move(dst, pdf)
            return True
    except subprocess.TimeoutExpired:
        pass
    finally:
        src.unlink(missing_ok=True)
    return False


def _libreoffice(docx: Path, pdf: Path) -> bool:
    for soffice in ("soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf.parent), str(docx)],
                           check=True, capture_output=True, timeout=120)
            if pdf.exists():
                return True
        except Exception:
            continue
    return False


def to_pdf(docx: Path) -> Path | None:
    pdf = docx.with_suffix(".pdf")
    return pdf if (_word(docx, pdf) or _libreoffice(docx, pdf)) else None


def open_in_word(path: str | Path) -> bool:
    """Open a file in Word for hand editing (Word's own default for .docx when it isn't installed). False if it can't be opened."""
    cmd = ["open", "-a", str(WORD_APP), str(path)] if WORD_APP.exists() else ["open", str(path)]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def edited_since_pdf(docx: str | Path, pdf: str | Path) -> bool:
    """True when the Word file was saved after its PDF was made, so the PDF no longer shows what the Word file says."""
    docx, pdf = Path(docx), Path(pdf)
    return docx.exists() and pdf.exists() and docx.stat().st_mtime > pdf.stat().st_mtime + 1


def edited_since_render(docx: str | Path) -> bool:
    """True when the Word file was saved after the app made it (render.json is written last in each render folder)."""
    from .render import HISTORY_FILE
    docx = Path(docx)
    meta = docx.parent / HISTORY_FILE
    return docx.exists() and meta.exists() and docx.stat().st_mtime > meta.stat().st_mtime + 1


def page_count(pdf: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(pdf)).pages)


def page_images(pdf: str | Path, dpi: int = 110) -> list[bytes]:
    """A PNG per page, for an exact preview of the rendered file. Needs pdftoppm (brew install poppler); [] without it."""
    exe = shutil.which("pdftoppm") or next((p for p in ("/opt/homebrew/bin/pdftoppm", "/usr/local/bin/pdftoppm")
                                           if Path(p).exists()), None)
    if not exe or not Path(pdf).exists():
        return []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([exe, "-png", "-r", str(dpi), str(pdf), f"{tmp}/page"], capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return []
        return [p.read_bytes() for p in sorted(Path(tmp).glob("page*.png"))] if r.returncode == 0 else []
