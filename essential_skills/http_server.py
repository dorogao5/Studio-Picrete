"""Internal-only authenticated HTTP transport. No public ingress."""
import hmac
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway import decode, invoke, error, MAX_REQUEST_BYTES

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 4
    def __init__(self,address,token):
        if os.environ.get('ESSENTIAL_TOOLS_ENV')=='production':
            from pathlib import Path
            configured=os.environ.get('REFERENCE_DB_PATH')
            if not configured or not Path(configured).is_absolute() or not Path(configured).is_file():
                raise ValueError('production requires an absolute readable REFERENCE_DB_PATH private mount')
        if not isinstance(token,str) or len(token)<32 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('ESSENTIAL_TOOLS_GATEWAY_TOKEN must be an ASCII token of at least 32 characters')
        self.token = token
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address,Handler)
    def get_request(self):
        sock,addr = super().get_request()
        sock.settimeout(2)
        return sock,addr
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise
    def process_request_thread(self,request,address):
        try: super().process_request_thread(request,address)
        finally: self.slots.release()
    def handle_error(self,*args): pass  # never log request headers/secrets

class Handler(BaseHTTPRequestHandler):
    server_version = 'EssentialGateway'
    def setup(self):
        super().setup()
        # Absolute connection deadline also defeats trickle/slowloris headers.
        self.deadline = threading.Timer(9,self.abort)
        self.deadline.daemon=True
        self.deadline.start()
    def abort(self):
        try: self.connection.shutdown(socket.SHUT_RDWR)
        except OSError: pass
    def finish(self):
        self.deadline.cancel()
        super().finish()
    def reply(self,code,payload):
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Connection','close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection=True
    def do_GET(self):
        self.reply(200,{'status':'ok'}) if self.path=='/health' else self.reply(404,error('NotFound','not found'))
    def do_POST(self):
        if self.path!='/invoke': return self.reply(404,error('NotFound','not found'))
        auth=self.headers.get_all('Authorization',[])
        if len(auth)!=1 or not hmac.compare_digest(auth[0].encode(),('Bearer '+self.server.token).encode()):
            return self.reply(401,error('Unauthorized','authentication required'))
        lengths=self.headers.get_all('Content-Length',[])
        if self.headers.get_all('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0])>8:
            return self.reply(400,error('InvalidRequest','one bounded Content-Length required; no transfer encoding'))
        size=int(lengths[0])
        if not 0<size<=MAX_REQUEST_BYTES: return self.reply(413,error('InvalidRequest','request size limit'))
        if self.headers.get_content_type()!='application/json': return self.reply(415,error('InvalidRequest','application/json required'))
        try:
            raw=self.rfile.read(size)
            if len(raw)!=size: raise ValueError('truncated request')
            request=decode(raw)
        except (ValueError,UnicodeError,RecursionError): return self.reply(400,error('InvalidRequest','malformed JSON request'))
        except (TimeoutError,OSError): return self.reply(408,error('RequestTimeout','body read timeout'))
        result=invoke(request)
        self.reply(503 if result.get('error_type')=='Busy' else 200,result)
    def log_message(self,*args): pass

if __name__=='__main__':
    Server((os.environ.get('ESSENTIAL_TOOLS_BIND','127.0.0.1'),8080),os.environ.get('ESSENTIAL_TOOLS_GATEWAY_TOKEN')).serve_forever()
