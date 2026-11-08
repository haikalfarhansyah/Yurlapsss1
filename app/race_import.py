"""Import reviewed LapBeeps lap tables, retaining their rounded precision."""
import datetime
import hashlib
import json
import uuid

from .race_results import result_rows


def import_lapbeeps(store, document, track_id):
    if not isinstance(document, dict) or document.get('format') != 'yurlaps-lapbeeps-v1':
        raise ValueError('Pilih file JSON hasil konversi LapBeeps YurLaps')
    club_id = store.club().get('club_id')
    if not club_id:
        raise ValueError('Login dan pilih club sebelum mengimpor')
    uuid.UUID(club_id)
    track = store.track(track_id)
    if not track or track.get('deleted'):
        raise ValueError('Pilih track yang masih aktif di menu Track')
    started = datetime.datetime.fromisoformat(document['started_at'])
    if started.tzinfo is None:
        raise ValueError('Tanggal impor harus menyertakan zona waktu')
    name = str(document.get('name') or '').strip()
    raw = document.get('drivers')
    if not name or len(name) > 200 or not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise ValueError('Nama sesi atau daftar peserta tidak valid')
    digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    rid = str(uuid.uuid5(uuid.UUID(club_id), 'lapbeeps:' + digest))
    if store.race(rid):
        return {'id': rid, 'already_imported': True}
    rows, numbers = [], set()
    for item in raw:
        no, driver = item.get('no'), str(item.get('name') or '').strip()
        transponder = item.get('transponder')
        laps = item.get('raw_laps_ms')
        if type(no) is not int or no < 1 or no in numbers or not driver or len(driver) > 120:
            raise ValueError('Nomor/nama driver tidak valid atau duplikat')
        numbers.add(no)
        if transponder is not None and (type(transponder) is not int or transponder <= 0):
            raise ValueError('ID transponder tidak valid')
        if not isinstance(laps, list) or len(laps) > 10000 or any(
                type(v) is not int or v <= 0 or v > 86400000 for v in laps):
            raise ValueError('Waktu lap tidak valid')
        origin = laps[0] if laps else None
        full_laps = laps[1:]
        crossing, passings = origin or 0, []
        for seq, lap in enumerate(full_laps, 1):
            crossing += lap
            passings.append({'seq': seq, 'lap_ms': lap, 'decoder_timecode': crossing,
                             'source': 'pdf_reconstructed', 'speed_kmh': None})
        # SQLite's legacy lap table requires a transponder value. Use a
        # negative, clearly synthetic ID so imported PDF data can never be
        # mistaken for a real transponder or linked account.
        synthetic_tp = -no if transponder is None else transponder
        status = str(item.get('result_status') or ('FIN' if full_laps else 'DNS')).upper()
        if status not in ('FIN', 'DNF', 'DNS'):
            status = 'FIN' if full_laps else 'DNS'
        # DNS has no race participation and stays out of personal history.
        member = store.member_for_transponder(transponder) if transponder and full_laps and status != 'DNS' else None
        account_id = (member or {}).get('id') if member else None
        rows.append({'uid': str(uuid.uuid5(uuid.UUID(rid), str(no))), 'no': no,
                     'name': (member or {}).get('name') or driver,
                     'nick': (member or {}).get('nick') or driver,
                     'transponder': synthetic_tp,
                     'account_id': account_id, 'member_id': account_id,
                     'laps': full_laps, 'passings': passings,
                     'first_decoder_ts': origin,
                     'last_decoder_ts': crossing if laps else None,
                     'last_race_crossing_ts': crossing if laps else None,
                     'result_status': status,
                     '_synthetic_transponder': synthetic_tp})
    cfg = {'name': name, 'start_mode': 'grid', 'rank': 'position', 'target': 'laps',
           'value': max(len(d['laps']) for d in rows), 'lap_storage_version': 2}
    drivers = result_rows(rows, cfg)
    for driver in drivers:
        driver.pop('_synthetic_transponder', None)
    race = {'id': rid, 'name': name, 'started_at': started.timestamp(),
            'ended_at': started.timestamp() + max((d['last_decoder_ts'] or 0) for d in rows)/1000,
            'track_id': track['id'], 'track_name': track['name'], 'cfg': cfg,
            'drivers': drivers, 'status': 'saved', 'sync': 'belum',
            'source': {'type': 'lapbeeps_pdf', 'file_sha256': document.get('pdf_sha256'),
                       'precision_ms': 10, 'first_passing_removed': True,
                       'account_mapping': 'unlinked', 'timestamps': 'reconstructed'},
            'log': []}
    store.simpan_race(race)
    valid = [d for d in drivers if d.get('laps') and d.get('best_ms')]
    if valid:
        best = min(valid, key=lambda d: d['best_ms'])
        store.perbarui_rekor(track['id'], best['best_ms'], best.get('nick') or best['name'])
    return {'id': rid, 'already_imported': False}
