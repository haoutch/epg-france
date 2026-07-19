#!/usr/bin/env python3
from __future__ import annotations
import gzip, json, lzma, io, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
IDS_FILE = ROOT / "config" / "channel_ids.txt"
SOURCES = [
    "https://epg.pw/xmltv/epg_FR.xml.gz",
]

def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "EPG-France-GitHub/1.0"})
    with urllib.request.urlopen(req, timeout=240) as response:
        data = response.read()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith(".xml"))
            return archive.read(name)
    if data[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(data)
    return data

def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    ids = {line.strip() for line in IDS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}
    errors = []
    raw = None
    source = None
    for candidate in SOURCES:
        try:
            candidate_raw = download(candidate)
            if b"<tv" not in candidate_raw[:2000]:
                raise ValueError("La source reçue n'est pas un fichier XMLTV")
            raw, source = candidate_raw, candidate
            break
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    if raw is None:
        raise RuntimeError("Toutes les sources ont échoué: " + " | ".join(errors))

    root = ET.fromstring(raw)
    output = ET.Element("tv", root.attrib)
    found_ids = set()
    programmes = 0
    for node in root:
        channel_id = node.attrib.get("id") if node.tag == "channel" else node.attrib.get("channel")
        if channel_id in ids:
            output.append(node)
            if node.tag == "channel":
                found_ids.add(channel_id)
            elif node.tag == "programme":
                programmes += 1

    xml = ET.tostring(output, encoding="utf-8", xml_declaration=True)
    (PUBLIC / "epg_france.xml").write_bytes(xml)
    with gzip.open(PUBLIC / "epg_france.xml.gz", "wb", compresslevel=9) as archive:
        archive.write(xml)

    status = {
        "ok": True,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "requested_channel_ids": len(ids),
        "matched_channel_ids": len(found_ids),
        "programmes": programmes,
        "unmatched_channel_ids": sorted(ids - found_ids),
    }
    (PUBLIC / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
