import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from tools import dispatch

def test_calculator_is_deterministic_and_safe():
    a = dispatch("calculator", {"expression": "sqrt(9)+exp(0)"})
    assert a["normalized_result"]["value"] == "4"
    assert dispatch("calculator", {"expression": "__import__('os')"})["status"] == "error"

def test_sympy_identity():
    out = dispatch("sympy", {"operation":"identity", "left":"(x+1)**2", "right":"x**2+2*x+1", "symbols":["x"]})
    assert out["normalized_result"]["result"] is True

def test_reference_lookup(monkeypatch):
    monkeypatch.setenv('REFERENCE_DB_PATH',str(Path(__file__).with_name('reference_fixture.csv')))
    out = dispatch("reference_db", {"reference_id":"synthetic_rate"})
    assert out["status"] == "success"
    assert out["normalized_result"]["record"]["unit"] == "s^-1"

def test_reference_alias_returns_only_canonical_record(monkeypatch, tmp_path):
    import csv
    import hashlib
    import json
    path = tmp_path / 'private.csv'
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['reference_id', 'value', 'unit', 'conditions', 'aliases'])
        writer.writeheader()
        writer.writerow(dict(reference_id='synthetic_canonical', value='7', unit='J',
                             conditions='{}', aliases=json.dumps(['synthetic_old'])))
    monkeypatch.setenv('REFERENCE_DB_PATH', str(path))
    current = dispatch('reference_db', {'reference_id': 'synthetic_canonical'})
    legacy = dispatch('reference_db', {'reference_id': 'synthetic_old'})
    assert current['normalized_result'] == legacy['normalized_result']
    assert legacy['normalized_result']['record'] == dict(reference_id='synthetic_canonical', value='7', unit='J', conditions={})
    assert legacy['normalized_result']['database_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()

def test_reference_alias_collision_fails_closed(monkeypatch, tmp_path):
    import csv
    path = tmp_path / 'private.csv'
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['reference_id', 'conditions', 'aliases'])
        writer.writeheader()
        writer.writerow(dict(reference_id='synthetic_a', conditions='{}', aliases='["synthetic_b"]'))
        writer.writerow(dict(reference_id='synthetic_b', conditions='{}', aliases='[]'))
    monkeypatch.setenv('REFERENCE_DB_PATH', str(path))
    assert dispatch('reference_db', {'reference_id': 'synthetic_b'})['status'] == 'error'
