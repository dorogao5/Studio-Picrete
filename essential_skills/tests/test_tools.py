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
