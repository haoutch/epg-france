#!/usr/bin/env python3
import csv, gzip, io, json, re, sys, urllib.request, unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'config'
OUT = ROOT
SOURCE_URL = 'https://epg.pw/xmltv/epg_FR.xml.gz'
TIME_SHIFT_HOURS = -8
MIN_FUZZY = 0.90
MIN_MARGIN = 0.08


def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii','ignore').decode().lower()
    s = re.sub(r'\b(fhd|uhd|sd|hd|4k)\b', ' ', s)
    s = re.sub(r'\b(fr|france)\s*\|\s*', ' ', s)
    s = re.sub(r'\b(canals?|mycanal)\b', 'canal', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def norm_id(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def load_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f, delimiter=';'))


def load_aliases(path):
    aliases = {}
    if not path.exists():
        return aliases
    for r in load_csv(path):
        aliases[norm(r['playlist_name'])] = r['source_name']
    return aliases


def download_source(dest):
    req = urllib.request.Request(SOURCE_URL, headers={'User-Agent':'Mozilla/5.0 EPG builder'})
    with urllib.request.urlopen(req, timeout=90) as r, dest.open('wb') as f:
        f.write(r.read())


def parse_channel_index(gz_path):
    by_id = {}
    by_name = {}
    with gzip.open(gz_path, 'rb') as fh:
        for _, elem in ET.iterparse(fh, events=('end',)):
            if elem.tag != 'channel':
                continue
            cid = elem.get('id','')
            names = [x.text.strip() for x in elem.findall('display-name') if x.text and x.text.strip()]
            primary = names[0] if names else cid
            by_id[norm_id(cid)] = (cid, primary)
            for n in names:
                by_name.setdefault(norm(n), []).append((cid, n))
            elem.clear()
    return by_id, by_name


def choose_source(row, by_id, by_name, aliases):
    tid = row.get('tvg_id_original','')
    name = row.get('nom_chaine','')
    if tid and norm_id(tid) in by_id:
        return by_id[norm_id(tid)][0], 'original'
    n = norm(name)
    if n in aliases:
        target = norm(aliases[n])
        hits = by_name.get(target, [])
        if hits:
            return hits[0][0], 'manual_alias'
    hits = by_name.get(n, [])
    if len(hits) == 1:
        return hits[0][0], 'name_exact'
    # score all unique display names
    scored = []
    for nn, vals in by_name.items():
        score = SequenceMatcher(None, n, nn).ratio()
        if score >= MIN_FUZZY:
            scored.append((score, nn, vals))
    scored.sort(reverse=True, key=lambda x:x[0])
    if scored and (len(scored)==1 or scored[0][0]-scored[1][0] >= MIN_MARGIN):
        return scored[0][2][0][0], f'fuzzy:{scored[0][0]:.3f}'
    return '', 'unmapped'


def shift_ts(ts):
    if not ts:
        return ts
    m = re.match(r'^(\d{14})(\s*[+-]\d{4})?$', ts.strip())
    if not m:
        return ts
    dt = datetime.strptime(m.group(1), '%Y%m%d%H%M%S') + timedelta(hours=TIME_SHIFT_HOURS)
    return dt.strftime('%Y%m%d%H%M%S') + (m.group(2) or '')


def main():
    mapping_path = CONFIG / 'mapping_chaines.csv'
    alias_path = CONFIG / 'manual_aliases.csv'
    rows = load_csv(mapping_path)
    aliases = load_aliases(alias_path)
    wanted = {r['tvg_id_final']: r for r in rows if r.get('tvg_id_final')}
    if not wanted:
        raise SystemExit('Aucun tvg_id_final exploitable dans mapping_chaines.csv')

    gz_path = ROOT / 'epg_source.xml.gz'
    print(f'Download {SOURCE_URL}')
    download_source(gz_path)
    by_id, by_name = parse_channel_index(gz_path)

    resolved = {}
    methods = {}
    source_ids = set()
    for tid, row in wanted.items():
        sid, method = choose_source(row, by_id, by_name, aliases)
        if sid:
            resolved[tid] = sid
            methods[tid] = method
            source_ids.add(sid)

    # Write resolution report.
    report = ROOT / 'mapping_result.csv'
    with report.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['tvg_id','nom_chaine','source_channel_id','methode'])
        for tid, row in wanted.items():
            w.writerow([tid, row['nom_chaine'], resolved.get(tid,''), methods.get(tid,'unmapped')])

    # Stream source a second time, keeping only matched channels/programmes.
    out_path = ROOT / 'epg_france.xml'
    root = ET.Element('tv')
    root.set('generator-info-name', 'EPG France - epg.pw')
    with gzip.open(gz_path, 'rb') as fh:
        for _, elem in ET.iterparse(fh, events=('end',)):
            if elem.tag == 'channel':
                sid = elem.get('id','')
                if sid in source_ids:
                    # determine destination ID
                    dst = next((k for k,v in resolved.items() if v == sid), None)
                    if dst:
                        elem.set('id', dst)
                        root.append(elem)
                        elem.clear()
                    else:
                        elem.clear()
                else:
                    elem.clear()
            elif elem.tag == 'programme':
                sid = elem.get('channel','')
                dst = next((k for k,v in resolved.items() if v == sid), None)
                if dst:
                    elem.set('channel', dst)
                    if 'start' in elem.attrib: elem.set('start', shift_ts(elem.get('start')))
                    if 'stop' in elem.attrib: elem.set('stop', shift_ts(elem.get('stop')))
                    root.append(elem)
                elem.clear()
    ET.ElementTree(root).write(out_path, encoding='utf-8', xml_declaration=True)
    with open(out_path, 'rb') as f_in, gzip.open(ROOT / 'epg_france.xml.gz','wb',compresslevel=9) as f_out:
        f_out.write(f_in.read())

    channels = len(root.findall('channel'))
    programmes = len(root.findall('programme'))
    unmatched = len(wanted) - len(resolved)
    status = {
        'ok': channels > 0 and programmes > 0,
        'source': SOURCE_URL,
        'requested_channel_ids': len(wanted),
        'matched_channel_ids': len(resolved),
        'unmatched_channel_ids': unmatched,
        'channels': channels,
        'programmes': programmes,
        'time_shift_hours': TIME_SHIFT_HOURS,
        'matching_methods': {m: list(methods.values()).count(m) for m in sorted(set(methods.values()))},
        'generated_at_utc': datetime.utcnow().isoformat(timespec='seconds') + 'Z'
    }
    (ROOT/'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    if not status['ok']:
        raise SystemExit('EPG invalide: aucun programme exploitable.')
    print(json.dumps(status, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
