"""Input-bound simulation checkpoints; a filename alone is never completion proof."""
import hashlib
import json
import os
from pathlib import Path


def file_digest(path):
    digest = hashlib.sha256()
    if not os.path.isfile(path):
        return 'missing'
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_meta(path):
    # The exporter writes meta first. Avoid loading millions of trade records
    # merely to check whether their input generation matches.
    try:
        with open(path, encoding='utf-8') as handle:
            prefix = handle.read(8192)
        marker = prefix.index('"meta"')
        value = prefix.index(':', marker) + 1
        return json.JSONDecoder().raw_decode(prefix[value:].lstrip())[0]
    except (OSError, ValueError, TypeError):
        return {}


def generation_id(last_date, paths):
    material = {'last_date': str(last_date), 'inputs': {str(p): file_digest(p) for p in paths}}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def checkpoint_matches(path, generation, signature):
    meta = read_meta(path)
    return (meta.get('generation') == generation and
            meta.get('signal_signature') == signature)


def resumable_configs(data_dir, generation):
    directory = Path(data_dir)
    try:
        meta = json.loads((directory / 'sim_meta.json').read_text())
        if (meta.get('generation') == generation and meta.get('ohlc_digest') and
                meta['ohlc_digest'] == file_digest(directory / 'trade_ohlc.json')):
            return set(meta.get('configs', []))
    except (OSError, ValueError, TypeError):
        pass
    return set()


def write_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, default=str)
    os.replace(temporary, path)


def validate_publication(data_dir='docs/data'):
    directory = Path(data_dir)
    meta = json.loads((directory / 'sim_meta.json').read_text())
    expected = meta.get('expected_configs')
    if not expected or set(meta.get('configs', [])) != set(expected):
        raise ValueError('Simulation incomplete: not all expected configurations are current')
    if not meta.get('generation'):
        raise ValueError('Simulation has no input-generation proof')
    for cid in expected:
        item = read_meta(directory / ('sim_results_' + cid + '.json'))
        if item.get('generation') != meta['generation'] or item.get('last_date') != meta['last_date']:
            raise ValueError('Stale configuration would be published: ' + cid)
    if not (directory / 'trade_ohlc.json').is_file():
        raise ValueError('Trade price history is missing')
    if meta.get('ohlc_digest') != file_digest(directory / 'trade_ohlc.json'):
        raise ValueError('Trade price history is incomplete or from another checkpoint')
    print('Freshness verified:', len(expected), 'configurations through', meta['last_date'])


if __name__ == '__main__':
    validate_publication()
