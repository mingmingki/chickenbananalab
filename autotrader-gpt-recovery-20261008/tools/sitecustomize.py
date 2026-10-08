"""Local verification only: fail closed before any socket network access."""
import os
import socket
from pathlib import Path

def _deny(*args, **kwargs):
    path = Path(os.environ['AUTOTRADER_PROJECT_DIR']) / 'network-blocked.log'
    with path.open('a') as handle:
        handle.write('Blocked socket network attempt\n')
    raise RuntimeError('External network disabled for isolated audit tests')

socket.socket.connect = _deny
socket.socket.connect_ex = _deny
socket.socket.sendto = _deny
socket.create_connection = _deny
