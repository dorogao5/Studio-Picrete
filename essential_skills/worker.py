"""Private one-shot worker; not a user-supplied Python execution interface."""
import resource
import sys
from pathlib import Path

resource.setrlimit(resource.RLIMIT_CPU,(4,5))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
# RLIMIT_AS is effective on Linux (production); macOS uses container memory limits instead.
if sys.platform.startswith('linux'):
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import json
from gateway import decode, error, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
from tools import dispatch

try:
    request = decode(sys.stdin.buffer.read(MAX_REQUEST_BYTES+1))
    result = dispatch(request['tool'],request['arguments'])
    output = json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
    if len(output)>MAX_RESPONSE_BYTES:
        output=json.dumps(error('OutputLimit','result too large')).encode()
except Exception:
    output=json.dumps(error('WorkerFailure','worker could not compute a finite result')).encode()
sys.stdout.buffer.write(output)
