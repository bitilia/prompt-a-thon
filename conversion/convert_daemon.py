import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [convert_daemon] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("convert_daemon")

CONVERSION_DIR = Path(os.environ.get("CONVERSION_DIR", "/opt/prompt-a-thon/conversion"))
SOCKET_PATH    = Path(os.environ.get("CONVERT_SOCKET", str(CONVERSION_DIR / "convert.sock")))
MAX_WORKERS    = 3
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
_sem = threading.Semaphore(MAX_WORKERS)

def _clean_env():

    return {
        "PATH":   "/usr/bin:/bin:/usr/local/bin",
        "HOME":   tempfile.gettempdir(),
        "LANG":   "en_US.UTF-8",
        "TMPDIR": tempfile.gettempdir(),
    }

def _unshare_wrap(cmd):

    return cmd

def recvall(sock, n):

    buf = b""
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError("socket closed before receiving all bytes")
        buf += chunk
    return buf

def recv_line(sock):

    buf = b""
    while True:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("socket closed")
        if ch == b"\n":
            return buf.decode(errors="replace").strip()
        buf += ch
        if len(buf) > 4096:
            raise ValueError("header line too long")

def convert_docx(docx_bytes: bytes) -> tuple[bool, bytes, str]:

    lo = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo:
        return False, b"", "LibreOffice not installed"

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        src = tmp / "input.docx"
        src.write_bytes(docx_bytes)
        profile = tmp / "profile"
        profile.mkdir()
        outd = tmp / "out"
        outd.mkdir()

        cmd = _unshare_wrap([
            lo, "--headless", "--norestore",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to", "pdf:writer_pdf_Export",
            "--outdir", str(outd),
            str(src),
        ])
        try:
            r = subprocess.run(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, close_fds=True,
                env=_clean_env(), timeout=90,
            )
        except subprocess.TimeoutExpired:
            return False, b"", "conversion timed out"
        except Exception as e:
            return False, b"", str(e)

        if r.returncode != 0:
            return False, b"", f"LO rc={r.returncode}: {r.stderr.decode(errors='replace')[:200]}"

        produced = list(outd.glob("*.pdf"))
        if not produced or produced[0].stat().st_size == 0:
            return False, b"", f"LO produced no output: {r.stdout.decode(errors='replace')[:100]}"

        pdf_bytes = produced[0].read_bytes()
        return True, pdf_bytes, ""

def handle_client(conn: socket.socket):
    try:

        header = recv_line(conn)
        parts = header.split(" ", 2)
        if len(parts) != 3 or parts[0] != "CONVERT":
            conn.sendall(b"ERROR bad_request invalid protocol\n")
            return

        _, job_id, size_str = parts
        try:
            file_size = int(size_str)
        except ValueError:
            conn.sendall(f"ERROR {job_id} invalid size\n".encode())
            return

        if file_size > MAX_INPUT_BYTES:
            conn.sendall(f"ERROR {job_id} file too large\n".encode())
            return

        docx_bytes = recvall(conn, file_size)

        acquired = _sem.acquire(timeout=120)
        if not acquired:
            conn.sendall(f"ERROR {job_id} server busy\n".encode())
            return

        try:
            ok, pdf_bytes, err = convert_docx(docx_bytes)
        finally:
            _sem.release()

        if ok:
            if len(pdf_bytes) > MAX_OUTPUT_BYTES:
                conn.sendall(f"ERROR {job_id} output too large\n".encode())
                return
            logger.info("OK job=%s pdf_size=%d", job_id, len(pdf_bytes))
            header_out = f"OK {job_id} {len(pdf_bytes)}\n".encode()
            conn.sendall(header_out + pdf_bytes)
        else:
            logger.warning("ERROR job=%s err=%s", job_id, err)
            conn.sendall(f"ERROR {job_id} {err}\n".encode())

    except Exception as e:
        logger.error("handle_client error: %s", e)
        try:
            conn.sendall(b"ERROR internal server error\n")
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

def run_server():
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    SOCKET_PATH.chmod(0o660)
    server.listen(64)
    logger.info("listening on %s", SOCKET_PATH)

    def _shutdown(sig, _):
        logger.info("shutting down")
        server.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        try:
            conn, _ = server.accept()
            t = threading.Thread(target=handle_client, args=(conn,), daemon=True)
            t.start()
        except OSError:
            break

if __name__ == "__main__":
    run_server()
