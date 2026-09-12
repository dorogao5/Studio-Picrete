"""Local JSONL adapter using the same isolated gateway, not direct dispatch."""
import json
import sys
from gateway import decode, invoke, error, MAX_REQUEST_BYTES

def main():
    while True:
        line=sys.stdin.buffer.readline(MAX_REQUEST_BYTES+1)
        if not line: return
        if len(line)>MAX_REQUEST_BYTES:
            print(json.dumps(error('InvalidRequest','line too large')),flush=True)
            return  # do not interpret remaining fragments as new requests
        if not line.strip(): continue
        try: result=invoke(decode(line))
        except (ValueError,UnicodeError,RecursionError): result=error('InvalidRequest','malformed JSON request')
        print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)

if __name__=='__main__': main()
