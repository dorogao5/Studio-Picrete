"""Bounded process boundary shared by HTTP and JSONL adapters."""
import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

MAX_REQUEST_BYTES = 32768
MAX_RESPONSE_BYTES = 131072
COMPUTE_TIMEOUT = 5.0
SLOTS = threading.BoundedSemaphore(4)
ROOT = Path(__file__).resolve().parent
VERSIONS = {'calculator':'scientific-calculator-v3','sympy':'ast-sympy-v2','reference_db':'reference-db-private-v2'}

def bind(request, result):
    tool, args = request['tool'], request['arguments']
    version=VERSIONS[tool]
    payload=json.dumps({'tool':tool,'version':version,'arguments':args},sort_keys=True,ensure_ascii=False,separators=(',',':'))
    result.update(tool_name=tool,tool_version=version,arguments=args,
                  trace_id=tool+':'+hashlib.sha256(payload.encode()).hexdigest()[:16])
    return result

def error(kind, message):
    return {'status':'error','normalized_result':None,'error_type':kind,'error':message}

def decode(raw):
    if len(raw)>MAX_REQUEST_BYTES: raise ValueError('request too large')
    def pairs(items):
        result = {}
        for k,v in items:
            if k in result: raise ValueError('duplicate JSON key')
            result[k] = v
        return result
    def invalid(value): raise ValueError('nonfinite JSON number')
    request = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(request,dict) or set(request)-{'tool','arguments'} or request.get('tool') not in ('calculator','sympy','reference_db') or not isinstance(request.get('arguments'),dict):
        raise ValueError('expected tool and arguments object')
    return request

def run_process(request, timeout=COMPUTE_TIMEOUT):
    # Fixed executable/script; request is data on stdin, never a command.
    env = {'PATH':os.defpath,'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'}
    if 'REFERENCE_DB_PATH' in os.environ: env['REFERENCE_DB_PATH']=os.environ['REFERENCE_DB_PATH']
    process = subprocess.Popen([sys.executable,'-I','-B',str(ROOT/'worker.py')],stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                               cwd=ROOT,env=env,start_new_session=True)
    try:
        try:
            output,_ = process.communicate(json.dumps(request,allow_nan=False).encode(),timeout=timeout)
        except subprocess.TimeoutExpired:
            return error('ComputationTimeout','computation exceeded wall-clock budget')
        if process.returncode!=0: return error('ResourceLimit','worker terminated without a result')
        if len(output)>MAX_RESPONSE_BYTES: return error('OutputLimit','result too large')
        return json.loads(output)
    finally:
        # Kill the process group, including any descendants, then reap the leader.
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        for stream in (process.stdin,process.stdout):
            if stream: stream.close()

def invoke(request):
    try:
        request = decode(json.dumps(request,allow_nan=False).encode())
    except (ValueError,TypeError,RecursionError):
        return error('InvalidRequest','invalid tool request')
    if not SLOTS.acquire(blocking=False): return bind(request,error('Busy','gateway concurrency limit reached; retry later'))
    try:
        return bind(request,run_process(request))
    except Exception:
        return bind(request,error('WorkerFailure','worker could not complete the request'))
    finally:
        SLOTS.release()
