from __future__ import annotations

import io
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

def strip_metadata(path: Path) -> bool:

    try:
        suffix = path.suffix.lower().lstrip(".")
        if suffix == "pdf":
            return _strip_pdf(path)
        if suffix in ("docx", "xlsx", "pptx"):
            return _strip_ooxml(path)
        if suffix == "odt":
            return _strip_odt(path)
        if suffix == "csv":
            return True

        logger.info("strip_metadata: no handler for .%s; leaving as-is", suffix)
        return True
    except Exception as exc:
        logger.warning("strip_metadata failed on %s: %s", path, exc)
        return False

def _strip_pdf(path: Path) -> bool:

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        logger.info("strip_metadata: pypdf not installed; PDF kept as-is")
        return True

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        logger.warning("PDF parse failed (%s): %s", path, exc)
        return False

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    writer.add_metadata({})

    try:
        if "/Metadata" in writer._root_object:
            del writer._root_object["/Metadata"]
    except Exception:
        pass

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(path)
    return True

_BLANK_CORE_XML = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    b' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    b' xmlns:dcterms="http://purl.org/dc/terms/"'
    b' xmlns:dcmitype="http://purl.org/dc/dcmitype/"'
    b' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"></cp:coreProperties>'
)
_BLANK_APP_XML_DOCX = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
    b' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"></Properties>'
)

def _strip_ooxml(path: Path) -> bool:

    if not zipfile.is_zipfile(path):
        return False

    src_bytes = path.read_bytes()
    src = zipfile.ZipFile(io.BytesIO(src_bytes), "r")

    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            name = info.filename
            data = src.read(info)
            lower = name.lower()

            if lower == "docprops/core.xml":
                data = _BLANK_CORE_XML
            elif lower == "docprops/app.xml":
                data = _BLANK_APP_XML_DOCX
            elif lower == "docprops/custom.xml":
                continue
            elif lower.startswith("docprops/thumbnail"):
                continue

            new_info = zipfile.ZipInfo(filename=info.filename,
                                       date_time=(2000, 1, 1, 0, 0, 0))
            new_info.compress_type = info.compress_type or zipfile.ZIP_DEFLATED
            new_info.external_attr = info.external_attr
            dst.writestr(new_info, data)
    src.close()
    tmp.replace(path)
    return True

_META_TAG_RE = re.compile(rb"<office:meta\b[^>]*>.*?</office:meta>", re.DOTALL)

def _strip_odt(path: Path) -> bool:

    if not zipfile.is_zipfile(path):
        return False

    src_bytes = path.read_bytes()
    src = zipfile.ZipFile(io.BytesIO(src_bytes), "r")

    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.lower() == "meta.xml":
                data = _META_TAG_RE.sub(
                    b'<office:meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>',
                    data,
                )
            elif info.filename.lower().startswith("thumbnails/"):
                continue
            new_info = zipfile.ZipInfo(filename=info.filename,
                                       date_time=(2000, 1, 1, 0, 0, 0))
            new_info.compress_type = info.compress_type or zipfile.ZIP_DEFLATED
            new_info.external_attr = info.external_attr
            dst.writestr(new_info, data)
    src.close()
    tmp.replace(path)
    return True
