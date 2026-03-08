from __future__ import annotations

import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

ALLOWED_EXTENSIONS = {".cbz", ".cbr", ".pdf", ".epub"}
DEFAULT_TEMPLATE = "{Series}/{Volume}/{IssueNumber} - {Title}{ext}"
ROOT = Path(__file__).parent.resolve()


@dataclass
class ComicMetadata:
    path: str
    series: str = ""
    title: str = ""
    issue_number: str = ""
    volume: str = ""
    writer: str = ""
    publisher: str = ""
    year: str = ""
    summary: str = ""
    ext: str = ""


def iter_comic_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in ALLOWED_EXTENSIONS else []
    return sorted([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS])


def _safe_text(node: ET.Element | None) -> str:
    return node.text.strip() if node is not None and node.text else ""


def read_metadata(path: Path) -> ComicMetadata:
    md = ComicMetadata(path=str(path), ext=path.suffix)
    if path.suffix.lower() != ".cbz":
        return md
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "ComicInfo.xml" not in zf.namelist():
                return md
            root = ET.fromstring(zf.read("ComicInfo.xml"))
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return md

    md.series = _safe_text(root.find("Series"))
    md.title = _safe_text(root.find("Title"))
    md.issue_number = _safe_text(root.find("Number"))
    md.volume = _safe_text(root.find("Volume"))
    md.writer = _safe_text(root.find("Writer"))
    md.publisher = _safe_text(root.find("Publisher"))
    md.year = _safe_text(root.find("Year"))
    md.summary = _safe_text(root.find("Summary"))
    return md


def build_comicinfo_xml(md: ComicMetadata) -> bytes:
    root = ET.Element("ComicInfo")
    for tag, value in [
        ("Series", md.series),
        ("Title", md.title),
        ("Number", md.issue_number),
        ("Volume", md.volume),
        ("Writer", md.writer),
        ("Publisher", md.publisher),
        ("Year", md.year),
        ("Summary", md.summary),
    ]:
        node = ET.SubElement(root, tag)
        node.text = value.strip()
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


def write_metadata(path: Path, md: ComicMetadata) -> None:
    if path.suffix.lower() != ".cbz":
        return
    payload = build_comicinfo_xml(md)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as src, zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename != "ComicInfo.xml":
                dst.writestr(info, src.read(info.filename))
        dst.writestr("ComicInfo.xml", payload)
    os.replace(tmp, path)


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "unknown"


def make_output_path(base: Path, template: str, md: ComicMetadata) -> Path:
    values = asdict(md)
    aliases = {
        "Series": values.get("series", ""),
        "Title": values.get("title", ""),
        "IssueNumber": values.get("issue_number", ""),
        "Volume": values.get("volume", ""),
        "Writer": values.get("writer", ""),
        "Publisher": values.get("publisher", ""),
        "Year": values.get("year", ""),
        "Summary": values.get("summary", ""),
        "ext": values.get("ext", ""),
    }
    safe = {k: sanitize_filename(str(v)) for k, v in aliases.items()}
    return base / template.format(**safe).lstrip("/\\")


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            html = (ROOT / "templates/index.html").read_text(encoding="utf-8").replace("{{ default_template }}", DEFAULT_TEMPLATE)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/static/"):
            file_path = ROOT / path.lstrip("/")
            if file_path.exists():
                body = file_path.read_bytes()
                ctype = "text/css" if file_path.suffix == ".css" else "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        data = self._read_json()

        if path == "/api/scan":
            root = Path(data.get("path", "")).expanduser()
            if not root.exists():
                return self._json(400, {"error": "Path does not exist"})
            return self._json(200, {"items": [asdict(read_metadata(p)) for p in iter_comic_files(root)]})

        if path == "/api/save_metadata":
            updated = 0
            for raw in data.get("items", []):
                src = Path(raw["path"])
                if src.exists():
                    write_metadata(src, ComicMetadata(**raw))
                    updated += 1
            return self._json(200, {"updated": updated})

        if path == "/api/build_hardlinks":
            output_root = Path(data.get("output_root", "")).expanduser()
            template = data.get("template", DEFAULT_TEMPLATE)
            created = 0
            collisions: list[str] = []
            for raw in data.get("items", []):
                src = Path(raw["path"])
                if not src.exists():
                    continue
                dst = make_output_path(output_root, template, ComicMetadata(**raw))
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    collisions.append(str(dst))
                    continue
                os.link(src, dst)
                created += 1
            return self._json(200, {"created": created, "collisions": collisions})

        self.send_error(404)


def run() -> None:
    host = "0.0.0.0"
    port = 8000
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
