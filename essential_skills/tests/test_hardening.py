import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from decimal import Decimal, localcontext
from http.client import HTTPConnection
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
import gateway
from tools import dispatch
from http_server import Server

def calc(expression):
    out=gateway.invoke({'tool':'calculator','arguments':{'expression':expression}})
    assert out['status']=='success',out
    return float(out['normalized_result']['value'])

@pytest.mark.parametrize('expression,expected',[
    ('ln(4)/ln(2)',2), # formal initial-rate order
    ('8.314*ln(.1719/.005)/(1/300-1/340)',74996.94623153962),
    ('(0.1/0.5)^(0.1/(0.5-0.1))',.668740304976422),
    ('8.314*(ln(3790/140)-0.5*ln(600/500))/(1/500-1/600)',79996.92797712471),
    ('(20*2*6-30*2*6)/(30*2-20*6)',2), # MM two-point KM recovery
])
def test_five_kinetics(expression,expected):
    assert calc(expression)==pytest.approx(expected,rel=1e-12)

def test_units_collision_prefactor():
    # pi given explicitly to Decimal, M in kg/mol; m^3->L factor is 1000.
    value=calc('1000*6.022e23*3.141592653589793238462643383279503*(3.65e-10)^2*sqrt(8*8.314*500/(3.141592653589793238462643383279503*(.028*.032/(.028+.032))))')
    assert value==pytest.approx(2.122061873312e11,rel=1e-12)

def test_exact_literals_and_final_precision():
    with localcontext() as context:
        context.prec=7
        out=dispatch('calculator',{'expression':'1.234567890123456789012345678901234'})
        assert out['normalized_result']['value']=='1.234567890123456789012345678901234'
        out=dispatch('calculator',{'expression':'1/3'})
        assert out['normalized_result']['value']=='0.'+'3'*34
    assert calc('0.1+0.2-0.3')==0
    assert calc('27^(1/3)')==pytest.approx(3)

@pytest.mark.parametrize('expr',[
    '__import__("os").system("id")','x.__class__','x[0]',
    'Symbol("x")','(lambda: 1)()','[x for x in (1,2)]',
    'sin(x, evaluate=True)','True','0x10','open("x")',
    'globals()','foo(x)','x==1','{1:2}','(1,2)',
])
def test_malicious_ast(expr):
    assert dispatch('sympy',{'operation':'simplify','expression':expr,'symbols':['x']})['status']=='error'
    assert dispatch('calculator',{'expression':expr})['status']=='error'

@pytest.mark.parametrize('expr',['1/0','sqrt(-1)','ln(0)','exp(1e10)','1e10001','(-1)^0.5','1e-10000*1e-10000'])
def test_bad_math_is_error(expr):
    assert gateway.invoke({'tool':'calculator','arguments':{'expression':expr}})['status']=='error'

def test_symbolic_operations():
    cases=[({'operation':'identity','left':'sin(x)^2+cos(x)^2','right':'1'},True),
           ({'operation':'derivative','expression':'ln(x)'},'1/x'),
           ({'operation':'integral','expression':'2*x'},'x**2'),
           ({'operation':'solve','equation':'2*x-4'},'[2]'),
           ({'operation':'simplify','expression':'0.1+0.2-0.3'},'0')]
    for args,expected in cases:
        out=gateway.invoke({'tool':'sympy','arguments':dict(args,symbols=['x'],variable='x')})
        assert out['status']=='success',out
        assert out['normalized_result']['result']==expected
    assert dispatch('sympy',{'operation':'derivative','expression':'x','symbols':['x'],'variable':'y'})['status']=='error'

def test_identity_not_proven_is_inconclusive():
    out=gateway.invoke({'tool':'sympy','arguments':{'operation':'identity','left':'ln(x*y)','right':'ln(x)+ln(y)','symbols':['x','y']}})
    assert out['status']=='success'
    assert out['normalized_result']['result'] is False
    assert out['normalized_result']['interpretation']=='not_proven'
    assert out['normalized_result']['assumptions']=={'x':'real','y':'real'}

def test_bound_error_envelope(monkeypatch):
    request={'tool':'calculator','arguments':{'expression':'1/0'}}
    direct=dispatch(request['tool'],request['arguments'])
    normal=gateway.invoke(request)
    monkeypatch.setattr(gateway,'run_process',lambda request: gateway.error('ComputationTimeout','budget exceeded'))
    timeout=gateway.invoke(request)
    for out in (direct,normal,timeout):
        assert out['tool_name']=='calculator'
        assert out['arguments']==request['arguments']
        assert out['tool_version']=='scientific-calculator-v2'
        assert out['trace_id']==direct['trace_id']
        assert out['status']=='error'

def test_catalog_and_csv_exclusion():
    root=Path(__file__).parents[1]
    catalog=json.loads((root/'tool-definitions.json').read_text())
    assert 'enum' not in catalog['reference_db']['parameters']['properties']['reference_id']
    assert catalog['calculator']['parameters']['properties']['expression']['maxLength']==512
    assert not (root/'reference_db.csv').exists()
    assert 'reference_db.csv' not in (root/'Dockerfile').read_text()

def test_reference_provenance(monkeypatch):
    path=Path(__file__).with_name('reference_fixture.csv')
    monkeypatch.setenv('REFERENCE_DB_PATH',str(path))
    out=gateway.invoke({'tool':'reference_db','arguments':{'reference_id':'synthetic_energy'}})
    result=out['normalized_result']
    assert result['database_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['record']['source']=='test_fixture_v1'
    assert result['record']['unit']=='J mol^-1'
    assert result['record']['value']=='12000'
    assert result['record']['conditions']=={}
    assert dispatch('reference_db',{'reference_id':'../etc/passwd'})['status']=='error'

def test_private_table_optional(monkeypatch):
    configured=os.environ.get('ESSENTIAL_TEST_PRIVATE_REFERENCE_PATH')
    if not configured: pytest.skip('explicit local private table path not configured')
    import csv
    path=Path(configured)
    assert path.is_absolute()
    rows=list(csv.DictReader(path.open()))
    assert len(rows)==80 and len({r['reference_id'] for r in rows})==80
    monkeypatch.setenv('REFERENCE_DB_PATH',str(path))
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    for row in rows:
        out=dispatch('reference_db',{'reference_id':row['reference_id']})
        assert out['status']=='success'
        original=dict(row,conditions=json.loads(row['conditions']))
        assert out['normalized_result']['record']==original
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before

def test_timeout_kills_and_reaps(monkeypatch):
    actual=gateway.subprocess.Popen
    children=[]
    def capture(*args,**kwargs):
        p=actual(*args,**kwargs); children.append(p); return p
    monkeypatch.setattr(gateway.subprocess,'Popen',capture)
    start=time.monotonic()
    out=gateway.run_process({'tool':'sympy','arguments':{'operation':'simplify','expression':'2^(2^1000)'}},timeout=.05)
    assert out['status']=='error'
    assert out['error_type']=='ComputationTimeout'
    assert time.monotonic()-start<2
    assert children[0].poll() is not None
    with pytest.raises(ProcessLookupError): os.kill(children[0].pid,0)
    assert calc('2+2')==4

def test_concurrency_bound():
    for _ in range(4): assert gateway.SLOTS.acquire(False)
    try:
        out=gateway.invoke({'tool':'calculator','arguments':{'expression':'1'}})
        assert out['error_type']=='Busy'
    finally:
        for _ in range(4): gateway.SLOTS.release()

@pytest.mark.parametrize('raw',[b'{"tool":"calculator","tool":"sympy","arguments":{}}',b'[]',b'{"tool":"calculator","arguments":{"x":NaN}}',b'x'*32769])
def test_bad_requests(raw):
    with pytest.raises(ValueError): gateway.decode(raw)

@pytest.fixture
def http(monkeypatch):
    monkeypatch.delenv('ESSENTIAL_TOOLS_ENV',raising=False)
    token='test-only-token-'+'x'*32
    srv=Server(('127.0.0.1',0),token)
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    yield srv,token
    srv.shutdown();srv.server_close();thread.join()

def test_http_health_auth_math(http):
    srv,token=http
    conn=HTTPConnection(*srv.server_address,timeout=8)
    conn.request('GET','/health');r=conn.getresponse()
    assert r.status==200 and json.loads(r.read())=={'status':'ok'}
    for auth,expr,code,status in [('', '1',401,'error'),('Bearer '+token,'1/0',200,'error'),('Bearer '+token,'1+1',200,'success')]:
        conn=HTTPConnection(*srv.server_address,timeout=8)
        conn.request('POST','/invoke',json.dumps({'tool':'calculator','arguments':{'expression':expr}}),{'Authorization':auth,'Content-Type':'application/json'})
        r=conn.getresponse();assert r.status==code; assert json.loads(r.read())['status']==status

@pytest.mark.parametrize('headers',[
    'Content-Length: -1\r\n','Content-Length: 32769\r\n',
    'Content-Length: 2\r\nContent-Length: 2\r\n',
    'Transfer-Encoding: chunked\r\nContent-Length: 2\r\n',
])
def test_http_reject_framing(http,headers):
    srv,token=http
    with socket.create_connection(srv.server_address,timeout=3) as s:
        s.sendall(('POST /invoke HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer '+token+'\r\nContent-Type: application/json\r\n'+headers+'\r\n').encode())
        result=s.recv(4096)
        assert b' 400 ' in result or b' 413 ' in result

def test_http_slow_body_timeout(http):
    srv,token=http
    with socket.create_connection(srv.server_address,timeout=4) as s:
        s.sendall(('POST /invoke HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer '+token+'\r\nContent-Type: application/json\r\nContent-Length: 10\r\n\r\n{').encode())
        assert b' 408 ' in s.recv(4096)

def test_production_configuration(monkeypatch):
    monkeypatch.setenv('ESSENTIAL_TOOLS_ENV','production')
    monkeypatch.delenv('REFERENCE_DB_PATH',raising=False)
    with pytest.raises(ValueError): Server(('127.0.0.1',0),'x'*32)

def test_resource_exhaustion_default_budget():
    start=time.monotonic()
    out=gateway.invoke({'tool':'sympy','arguments':{'operation':'simplify','expression':'2^(2^1000)','symbols':[]}})
    assert out['status']=='error'
    assert out['tool_name']=='sympy' and out['trace_id']
    assert time.monotonic()-start<7
    assert calc('1+2')==3

def test_nesting_and_expression_bounds():
    for expression in ['1+'*256+'1', '('*80+'1'+')'*80, '+'.join(['1']*250)]:
        # Parentheses alone disappear from AST; unary/arithmetic depth is limited.
        if expression.startswith('('): expression='-'*80+'1'
        assert gateway.invoke({'tool':'calculator','arguments':{'expression':expression}})['status']=='error'

def test_missing_reference_error_does_not_disclose_path(monkeypatch):
    monkeypatch.setenv('REFERENCE_DB_PATH','/secret/path/not-to-disclose.csv')
    out=gateway.invoke({'tool':'reference_db','arguments':{'reference_id':'missing'}})
    assert out['status']=='error'
    assert '/secret/' not in json.dumps(out)
