"""Synthetic values only. These fixtures are NOT scientific reference data."""
import csv
import hashlib
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from tools import dispatch
import gateway

@pytest.fixture
def database(tmp_path,monkeypatch):
    rows=[]
    def row(id,entity,prop,value,conditions):
        rows.append(dict(reference_id=id,entity=entity,property=prop,value=value,unit='synthetic unit',conditions=json.dumps(conditions),source='SYNTHETIC TEST ONLY',uncertainty_or_warning='Not real scientific data',aliases='[]'))
    row('test_ksp','AgCl','solubility_product','9e-8',{'temperature':None})
    row('test_water_g','H2O(g)','standard_molar_formation_enthalpy','42',{'phase':'g','temperature':300,'temperature_unit':'K'})
    row('test_water_l','H2O(l)','standard_molar_formation_enthalpy','21',{'phase':'l','temperature':300,'temperature_unit':'K'})
    row('test_co','Co(s)','standard_molar_entropy','12',{'phase':'s'})
    row('test_CO','CO(g)','standard_molar_entropy','18',{'phase':'g'})
    row('test_ion','Cu^2+','standard_molar_entropy','7',{'solvent':'water'})
    row('test_pair','Cu^2+ + 2e- -> Cu','standard_electrode_potential','3',{'solvent':'water'})
    path=tmp_path/'synthetic.csv'
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
    monkeypatch.setenv('REFERENCE_DB_PATH',str(path))
    return path

def search(**args):
    out=dispatch('reference_db',args)
    assert out['status']=='success',out
    return out['normalized_result']

def test_formula_name_and_russian_property(database):
    for query in ['AgCl','silver chloride','хлорид серебра','произведение растворимости AgCl']:
        result=search(query=query,property='solubility_product')
        assert result['match_status']=='unique'
        assert result['record']['reference_id']=='test_ksp'
        assert result['database_sha256']==hashlib.sha256(database.read_bytes()).hexdigest()
        assert result['record']['value']=='9e-8'
        assert 'aliases' not in result['record']

def test_phase_and_temperature_are_not_guessed(database):
    assert search(query='H2O',property='standard_molar_formation_enthalpy')['match_status']=='ambiguous'
    r=search(query='воды',property='standard_molar_formation_enthalpy',phase='gas',temperature_k=300)
    assert r['record']['reference_id']=='test_water_g'
    assert search(query='H2O',phase='gas',temperature_k=301)['match_status']=='not_found'
    r=search(query='AgCl',temperature_k=298.15)
    assert r['match_status']=='conditions_unverified' and 'record' not in r
    assert r['candidates'][0]['conditions_warnings']==['unknown_temperature']

def test_formula_case_charge_and_pair(database):
    assert search(query='Co',property='standard_molar_entropy')['record']['reference_id']=='test_co'
    assert search(query='CO',property='standard_molar_entropy')['record']['reference_id']=='test_CO'
    assert search(query='Cu²⁺',property='standard_molar_entropy')['record']['reference_id']=='test_ion'
    assert search(query='Cu2+/Cu',property='standard_electrode_potential')['record']['reference_id']=='test_pair'

def test_missing_and_pagination(database):
    r=search(query='does not exist',property='solubility_product')
    assert r['match_status']=='not_found' and r['total_matches']==0 and r['candidates']==[] and 'record' not in r
    r=search(query='H2O',limit=1)
    assert r['match_status']=='ambiguous' and r['total_matches']==2 and r['next_offset']==1
    r2=search(query='H2O',limit=1,offset=1)
    assert r2['next_offset'] is None and r2['candidates']!=r['candidates']

@pytest.mark.parametrize('args',[{}, {'query':'AgCl','reference_id':'test_ksp'}, {'reference_id':'test_ksp','phase':'gas'}, {'query':'AgCl','limit':True}, {'query':'AgCl','offset':-1}, {'query':'AgCl','temperature_k':float('nan')}, {'query':'AgCl','temperature_k':0}, {'query':'AgCl','property':'invented'},{'query':'AgCl','phase':'plasma'}])
def test_invalid_arguments(database,args):
    assert dispatch('reference_db',args)['status']=='error'

def test_search_works_through_isolated_worker(database):
    out=gateway.invoke({'tool':'reference_db','arguments':{'query':'AgCl','property':'solubility_product'}})
    assert out['status']=='success',out
    assert out['normalized_result']['record']['reference_id']=='test_ksp'
