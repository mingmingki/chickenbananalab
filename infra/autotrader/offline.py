"""Pytest plugin loaded before collection. No live project, keys or sockets."""
import atexit
import os
import socket
import tempfile

_runtime = tempfile.TemporaryDirectory(prefix='autotrader-ci-')
atexit.register(_runtime.cleanup)
os.environ['AUTOTRADER_PROJECT_DIR'] = _runtime.name
for key in list(os.environ):
    if any(word in key.upper() for word in ('SECRET','TOKEN','PASSWORD','PASSPHRASE','API_KEY','CREDENTIAL')):
        os.environ.pop(key)

def deny(*args, **kwargs):
    raise RuntimeError('Network disabled during autotrader tests; use a local fake')

socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.socket.sendto = deny
socket.create_connection = deny
socket.getaddrinfo = deny
