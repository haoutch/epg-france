#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import io
import json
import lzma
import re
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
IDS_FILE = ROOT / "config" / "channel_ids.txt"
MAPPING_FILE = ROOT / "config" / "mapping_chaines.csv"
SOURCES = ["https://epg.pw/xmltv/epg_FR.xml.gz"]

# Noms alternatifs fréquents. La clé et les valeurs sont normalisées automatiquement.
MANUAL_ALIASES = {
    "tf1 series films": ["tf1 series films", "tf1 sf"],
    "france info": ["franceinfo", "france info"],
    "la chaine meteo": ["la chaine meteo", "lachainemeteo"],
    "canal plus": ["canal+", "canal plus"],
    "canal plus sport": ["canal+ sport", "canal plus sport"],
    "canal plus foot": ["canal+ foot", "canal plus foot"],
    "canal plus cinema": ["canal+ cinema", "canal plus cinema"],
    "bein sports 1": ["bein sports 1", "bein sport 1"],
    "bein sports 2": ["bein sports 2", "bein sport 2"],
    "bein sports 3": ["bein sports 3", "bein sport 3"],
    "rmc sport 1": ["rmc sport 1"],
    "rmc sport 2": ["rmc sport 2"],
    "equipe": ["l equipe", "lequipe", "la chaine l equipe"],
}

QUALITY_WORDS = {
    "fr", "fhd", "hd", "uhd", "4k", "sd", "hevc", "backup", "vip",
    "france", "multi", "source", "live", "channel", "chaine"
}


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "EPG-France-GitHub/2.0"})
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


def normalize(value: str | None) -> str:
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower().replace("+", " plus ").replace("&", " et ")
    value = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    words = [w for w in value.split() if w not in QUALITY_WORDS]
    return " ".join(words).strip()


def compact(value: str | None) -> str:
    return normalize(value).replace(" ", "")


def load_targets() -> tuple[set[str], dict[str, set[str]], dict[str, str]]:
    requested = {
        line.strip() for line in IDS_FILE.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    aliases: dict[str, set[str]] = defaultdict(set)
    preferred_name: dict[str, str] = {}

    with MAPPING_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            target_id = (row.get("tvg_id_final") or row.get("tvg_id_original") or "").strip()
            if not target_id or target_id not in requested:
                continue
            playlist_name = (row.get("nom_chaine") or "").strip()
            preferred_name.setdefault(target_id, playlist_name or target_id)
            for candidate in (playlist_name, target_id, target_id.rsplit(".", 1)[0]):
                n = normalize(candidate)
                if n:
                    aliases[target_id].add(n)
                    aliases[target_id].add(n.replace(" plus ", " "))

    for target_id in requested:
        preferred_name.setdefault(target_id, target_id.rsplit(".", 1)[0])
        base = normalize(target_id.rsplit(".", 1)[0])
        if base:
            aliases[target_id].add(base)

    for key, values in MANUAL_ALIASES.items():
        key_n = normalize(key)
        matching_ids = [tid for tid, vals in aliases.items() if key_n in vals]
        for tid in matching_ids:
            aliases[tid].update(normalize(v) for v in values)

    return requested, aliases, preferred_name


def source_names(channel: ET.Element) -> set[str]:
    names = {normalize(channel.attrib.get("id")), normalize(channel.attrib.get("id", "").rsplit(".", 1)[0])}
    for node in channel.findall("display-name"):
        names.add(normalize(node.text))
    return {n for n in names if n}


def best_match(names: set[str], aliases: dict[str, set[str]], already_used: set[str]) -> tuple[str | None, float, str]:
    # 1. Correspondance exacte, la plus sûre.
    exact = []
    compact_names = {n.replace(" ", "") for n in names}
    for target_id, target_aliases in aliases.items():
        if target_id in already_used:
            continue
        if names & target_aliases or compact_names & {a.replace(" ", "") for a in target_aliases}:
            exact.append(target_id)
    if len(exact) == 1:
        return exact[0], 1.0, "exact_name"

    # 2. Correspondance approchée, avec seuil élevé pour éviter les faux positifs.
    winner, winner_score = None, 0.0
    runner_up = 0.0
    for target_id, target_aliases in aliases.items():
        if target_id in already_used:
            continue
        score = max(
            (SequenceMatcher(None, src, alias).ratio() for src in names for alias in target_aliases),
            default=0.0,
        )
        if score > winner_score:
            runner_up = winner_score
            winner, winner_score = target_id, score
        elif score > runner_up:
            runner_up = score

    # Marge minimale entre le meilleur résultat et le suivant.
    if winner and winner_score >= 0.88 and winner_score - runner_up >= 0.04:
        return winner, winner_score, "fuzzy_name"
    return None, winner_score, "unmatched"


def clone_with_id(channel: ET.Element, target_id: str, fallback_name: str) -> ET.Element:
    clone = ET.fromstring(ET.tostring(channel, encoding="utf-8"))
    clone.set("id", target_id)
    if clone.find("display-name") is None:
        ET.SubElement(clone, "display-name").text = fallback_name
    return clone


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    requested, aliases, preferred_name = load_targets()

    errors: list[str] = []
    raw = None
    source = None
    for candidate in SOURCES:
        try:
            candidate_raw = download(candidate)
            if b"<tv" not in candidate_raw[:4000]:
                raise ValueError("La source reçue n'est pas un fichier XMLTV")
            raw, source = candidate_raw, candidate
            break
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    if raw is None:
        raise RuntimeError("Toutes les sources ont échoué: " + " | ".join(errors))

    root = ET.fromstring(raw)
    output = ET.Element("tv", root.attrib)

    source_to_target: dict[str, str] = {}
    match_details: list[dict[str, object]] = []
    used_targets: set[str] = set()

    for channel in root.findall("channel"):
        source_id = channel.attrib.get("id", "")
        names = source_names(channel)
        target_id, score, method = best_match(names, aliases, used_targets)
        if not target_id:
            continue
        used_targets.add(target_id)
        source_to_target[source_id] = target_id
        output.append(clone_with_id(channel, target_id, preferred_name[target_id]))
        match_details.append({
            "target_id": target_id,
            "source_id": source_id,
            "source_names": sorted(names),
            "method": method,
            "score": round(score, 3),
        })

    programmes = 0
    for programme in root.findall("programme"):
        source_id = programme.attrib.get("channel", "")
        target_id = source_to_target.get(source_id)
        if not target_id:
            continue
        clone = ET.fromstring(ET.tostring(programme, encoding="utf-8"))
        clone.set("channel", target_id)
        output.append(clone)
        programmes += 1

    try:
        ET.indent(output, space="  ")
    except AttributeError:
        pass
    xml = ET.tostring(output, encoding="utf-8", xml_declaration=True)
    (PUBLIC / "epg_france.xml").write_bytes(xml)
    with gzip.open(PUBLIC / "epg_france.xml.gz", "wb", compresslevel=9) as archive:
        archive.write(xml)

    unmatched = sorted(requested - used_targets)
    status = {
        "ok": bool(used_targets and programmes),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "requested_channel_ids": len(requested),
        "matched_channel_ids": len(used_targets),
        "programmes": programmes,
        "unmatched_channel_ids": unmatched,
        "matches": sorted(match_details, key=lambda item: str(item["target_id"])),
        "errors": errors,
    }
    (PUBLIC / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in status.items() if k != "matches"}, ensure_ascii=False, indent=2))

    # Un workflow vert avec une EPG vide serait trompeur : on échoue explicitement.
    if not status["ok"]:
        raise RuntimeError("Aucune EPG exploitable n'a été générée. Consulte public/status.json")


if __name__ == "__main__":
    main()
