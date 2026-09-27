from __future__ import annotations

import logging
import os
import shutil
import socket
import tempfile
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

CONVERSION_DIR = Path(os.environ.get("CONVERSION_DIR", "/opt/prompt-a-thon/conversion"))
SOCKET_PATH    = Path(os.environ.get("CONVERT_SOCKET", str(CONVERSION_DIR / "convert.sock")))
CONVERT_USER   = os.environ.get("CONVERT_USER", "promptathon-convert")

_MAGIC = {
    "pdf":  b"%PDF",
    "docx": b"PK\x03\x04",
}

MAX_CONVERSIONS  = 3
MAX_INPUT_BYTES  = 50 * 1024 * 1024
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
_sem = threading.Semaphore(MAX_CONVERSIONS)

class PipelineError(Exception):
    pass

def _check_magic(path: Path, ext: str) -> bool:
    sig = _MAGIC.get(ext)
    if not sig:
        return True
    try:
        with open(path, "rb") as f:
            return f.read(len(sig)) == sig
    except OSError:
        return False

def _recvall(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("socket closed prematurely")
        buf += chunk
    return buf

def _recv_line(sock) -> str:
    buf = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("socket closed")
        if ch == b"\n":
            return buf.decode(errors="replace").strip()
        buf += ch
        if len(buf) > 4096:
            raise ValueError("response header too long")

def _request_conversion(docx_bytes: bytes, timeout: int = 90) -> bytes:

    job_id = uuid.uuid4().hex

    if not SOCKET_PATH.exists():
        raise PipelineError(
            "The conversion service is not running. Please contact the event organisers."
        )

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(SOCKET_PATH))

        header = f"CONVERT {job_id} {len(docx_bytes)}\n".encode()
        sock.sendall(header + docx_bytes)

        response_header = _recv_line(sock)
        parts = response_header.split(" ", 2)

        if parts[0] == "ERROR":
            err = parts[2] if len(parts) >= 3 else response_header
            logger.error("daemon error job=%s: %s", job_id, err)
            if "busy" in err.lower():
                raise PipelineError(
                    "The server is busy processing other files. Please try again in a moment."
                )
            raise PipelineError(
                "Your file could not be converted. Please export as PDF from Word and upload that instead."
            )

        if parts[0] != "OK" or len(parts) < 3:
            raise PipelineError("Your file could not be converted. Please try again.")

        pdf_size = int(parts[2])
        if pdf_size > MAX_OUTPUT_BYTES:
            raise PipelineError("Your file is too large after conversion.")

        pdf_bytes = _recvall(sock, pdf_size)
        sock.close()
        return pdf_bytes

    except PipelineError:
        raise
    except socket.timeout:
        raise PipelineError(
            "Your file took too long to convert. Please try a simpler document or export as PDF from Word."
        )
    except Exception as e:
        logger.error("socket error job=%s: %s", job_id, e)
        raise PipelineError(
            "Your file could not be converted. Please try again or export as PDF from Word."
        )

def _normalize_pdf(data: bytes) -> bytes:

    try:
        import io
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(io.BytesIO(data))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:
        logger.warning("pdf normalize (pypdf) failed, continuing with original: %s", exc)
        return data

def _verify_pdf_safe(data: bytes | Path, label: str = "") -> None:

    try:
        import pikepdf, io
    except ImportError:
        raise PipelineError("File processing failed. Please contact the event organisers.")

    tag = f"[{label}] " if label else ""

    if isinstance(data, Path):
        try:
            size = data.stat().st_size
            raw = data.read_bytes()
        except OSError as e:
            raise PipelineError("Your file could not be verified. Please try again.")
    else:
        raw = data
        size = len(raw)

    if size < 64:
        logger.error("%sPDF too small (%d bytes)", tag, size)
        raise PipelineError("Your file could not be converted correctly. Please export as PDF from Word.")
    if size > MAX_OUTPUT_BYTES:
        raise PipelineError("Your file is too large after conversion. Please reduce its size.")
    if not raw[:8].startswith(b"%PDF"):
        logger.error("%sNon-PDF magic bytes from daemon: %r", tag, raw[:8])
        raise PipelineError("Your file could not be converted correctly. Please export as PDF from Word.")

    try:
        pdf = pikepdf.open(io.BytesIO(raw))
    except Exception as e:
        logger.error("%sPDF structural error: %s", tag, e)
        raise PipelineError("Your file could not be converted correctly. Please export as PDF from Word.")

    try:
        page_count = len(pdf.pages)
        if page_count < 1:
            raise PipelineError("Your converted file appears to be empty.")
        if page_count > 2000:
            raise PipelineError("Your file has too many pages. Please upload a shorter document.")
        if pdf.is_encrypted:
            logger.error("%sDaemon produced encrypted PDF", tag)
            raise PipelineError("Your file could not be processed. Please export as PDF from Word.")
    except PipelineError:
        raise
    except Exception as e:
        logger.error("%sError inspecting PDF: %s", tag, e)
        raise PipelineError("Your file could not be verified. Please try again.")
    finally:
        try:
            pdf.close()
        except Exception:
            pass

_DANGER_KEYS = {
    "/JS", "/JavaScript", "/Launch", "/URI", "/GoToR",
    "/EmbeddedFile", "/Filespec", "/EF", "/AA",
}
_AA_DANGER = {"/O", "/C", "/WC", "/WS", "/DS", "/WP", "/U"}

def _sanitise_pdf(src_bytes: bytes, dst: Path) -> tuple[bool, list]:

    try:
        import pikepdf, io
    except ImportError:
        raise PipelineError("File processing failed. Please contact the event organisers.")

    stripped: list = []

    def _strip(obj, path="", _depth=0):
        if _depth > 20 or not isinstance(obj, pikepdf.Dictionary):
            return
        for key in list(obj.keys()):
            if key in _DANGER_KEYS:
                del obj[key]; stripped.append(f"{path}{key}")
            elif key == "/AA":
                aa = obj[key]
                if isinstance(aa, pikepdf.Dictionary):
                    for sk in list(aa.keys()):
                        if sk in _AA_DANGER:
                            del aa[sk]; stripped.append(f"{path}/AA{sk}")
                    if not list(aa.keys()):
                        del obj[key]; stripped.append(f"{path}/AA(empty)")
            else:
                try: _strip(obj[key], f"{path}{key}.", _depth + 1)
                except Exception: pass

    pdf = pikepdf.open(io.BytesIO(src_bytes))
    _strip(pdf.Root, "/Root")
    for i, page in enumerate(pdf.pages):
        _strip(page.obj, f"/Page[{i}]")
    try:
        if "/Info" in pdf.trailer:
            info = pdf.trailer["/Info"]
            if isinstance(info, pikepdf.Dictionary):
                for k in list(info.keys()): del info[k]
                stripped.append("/Info(cleared)")
    except Exception: pass
    try:
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]; stripped.append("/Metadata(stream)")
    except Exception: pass
    pdf.save(dst, compress_streams=False,
             object_stream_mode=pikepdf.ObjectStreamMode.preserve)
    pdf.close()
    return bool(stripped), stripped

def purge_conversion_dir() -> tuple[int, int]:

    count, total = 0, 0
    if not CONVERSION_DIR.exists():
        return 0, 0
    for d in [CONVERSION_DIR / "tmp"]:
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size; f.unlink(); count += 1
                    except OSError: pass
    return count, total

def _convert_docx_lo(src: Path, out_pdf: Path, best_effort: bool = False) -> None:

    docx_bytes = src.read_bytes()
    pdf_bytes = _request_conversion(docx_bytes, timeout=90)
    out_pdf.write_bytes(pdf_bytes)

def process_upload(
    uploaded_path: Path,
    original_filename: str,
    upload_dir: Path,
    audit_fn,
) -> tuple[str, str, int]:
    ext = Path(original_filename).suffix.lower().lstrip(".")
    if ext not in ("pdf", "docx"):
        raise PipelineError("Only PDF and DOCX files are accepted.")
    if not _check_magic(uploaded_path, ext):
        raise PipelineError("File content does not match its extension.")

    acquired = _sem.acquire(timeout=60)
    if not acquired:
        raise PipelineError("The server is busy. Please try again in a moment.")

    try:
        if ext == "docx":
            docx_bytes = uploaded_path.read_bytes()
            if len(docx_bytes) > MAX_INPUT_BYTES:
                raise PipelineError("Your file is too large.")
            pdf_bytes = _request_conversion(docx_bytes)
        else:
            pdf_bytes = uploaded_path.read_bytes()

        pdf_bytes = _normalize_pdf(pdf_bytes)
        _verify_pdf_safe(pdf_bytes, label=f"{ext}-upload:{original_filename}")

        with tempfile.TemporaryDirectory() as tmpdir:
            san_path = Path(tmpdir) / "sanitised.pdf"
            try:
                dangerous, stripped = _sanitise_pdf(pdf_bytes, san_path)
            except PipelineError:
                raise
            except Exception as e:
                logger.error("pikepdf sanitise failed: %s", e)
                raise PipelineError("Your file could not be processed. Please try a different file.")

            _verify_pdf_safe(san_path, label=f"post-sanitise:{original_filename}")

            display_name = Path(original_filename).stem + ".pdf"
            if dangerous:
                detail = f"{original_filename} → stripped: {', '.join(stripped[:20])}"
                logger.warning("DANGER FILE: %s", detail)
                audit_fn("file.danger_file", detail=detail)
            else:
                logger.info("CLEAN FILE: %s", original_filename)
                audit_fn("file.clean_file", detail=original_filename)

            stored_name = f"{uuid.uuid4().hex}.pdf"
            final_path = upload_dir / stored_name
            upload_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(san_path), str(final_path))

    finally:
        _sem.release()

    return stored_name, display_name, final_path.stat().st_size
