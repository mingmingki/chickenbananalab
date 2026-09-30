"""Bounded read-only diagnosis of the single previously recovered VM target.

No port scan, credential printing, escalation, proxy, or permission changes.
Only a known harmless remote marker is accepted as remote-command evidence.
"""
import argparse
import datetime
import json
import socket
import subprocess
from pathlib import Path

HOST = '34.28.148.113'
USER = 'bagmingi'
PORT = 22
IDENTITY = '/Users/bagmingi/.ssh/google_compute_engine'
KNOWN_HOSTS = '/Users/bagmingi/.ssh/google_compute_known_hosts'
HOST_KEY_ALIAS = 'compute.2817124852683862637'
MARKER = 'CANDIDATE_READ_ONLY_ACCESS_V1'


def _error(exc):
    # OSError.strerror contains an OS error description, not arbitrary process
    # output, authentication material, or file contents.
    return {'status': 'BLOCKED', 'errno': exc.errno, 'reason': exc.strerror}


def probe_tcp(factory=socket.socket):
    result = {key: {'status': 'NOT_REACHED'} for key in
              ('socket_create', 'tcp_connect', 'ssh_authentication', 'remote_command')}
    try:
        connection = factory(socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        result['socket_create'] = _error(exc)
        return result
    result['socket_create'] = {'status': 'PASS'}
    try:
        connection.settimeout(5)
        connection.connect((HOST, PORT))
        result['tcp_connect'] = {'status': 'PASS'}
    except OSError as exc:
        result['tcp_connect'] = _error(exc)
    finally:
        connection.close()
    return result


def classify_ssh(returncode, stdout, stderr):
    status = 'SSH_FAILED_UNCLASSIFIED'
    evidence = None
    for pattern, classification in (
        ('Operation not permitted', 'TCP_BLOCKED'),
        ('Host key verification failed', 'HOST_KEY_FAILED'),
        ('REMOTE HOST IDENTIFICATION HAS CHANGED', 'HOST_KEY_FAILED'),
        ('Permission denied (publickey', 'AUTHENTICATION_FAILED'),
        ('Connection timed out', 'TCP_TIMEOUT'),
        ('Connection refused', 'TCP_REFUSED'),
    ):
        if pattern in stderr:
            status, evidence = classification, pattern
            break
    marker_seen = MARKER in stdout.splitlines()
    if marker_seen:
        status = 'REMOTE_COMMAND_PASS' if returncode == 0 else 'REMOTE_COMMAND_FAILED'
    elif returncode == 0:
        status = 'REMOTE_COMMAND_UNVERIFIED'
    return {'status': status, 'returncode': returncode, 'known_error': evidence,
            'authentication_verified': marker_seen, 'remote_marker_seen': marker_seen}


def probe_ssh(runner=subprocess.run):
    argv = ['ssh', '-F', '/dev/null', '-n', '-T', '-i', IDENTITY,
            '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ProxyCommand=none',
            '-o', 'ControlMaster=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'UserKnownHostsFile=' + KNOWN_HOSTS,
            '-o', 'HostKeyAlias=' + HOST_KEY_ALIAS, USER + '@' + HOST,
            "printf '%s\\n' " + MARKER]
    try:
        process = runner(argv, capture_output=True, text=True, timeout=12, check=False)
    except subprocess.TimeoutExpired:
        return {'status': 'SSH_PROCESS_TIMEOUT', 'authentication_verified': False,
                'remote_marker_seen': False}
    except OSError as exc:
        return {'status': 'SSH_PROCESS_NOT_STARTED', 'errno': exc.errno}
    return classify_ssh(process.returncode, process.stdout, process.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New local JSON evidence file; refuses overwrite')
    args = parser.parse_args()
    result = {'observed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'target': {'host': HOST, 'port': PORT, 'source': 'previous_local_gcloud_connection_metadata'},
              'declared_session_policy_at_preparation': {
                  'sandbox': 'workspace-write', 'network': 'restricted', 'approval': 'never',
                  'note': 'Recorded from the creating session; not a probe of future session settings'},
              'tcp_probe': probe_tcp(), 'ssh_probe': probe_ssh()}
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as handle:
            handle.write(encoded)
    print(encoded, end='')
    return 0 if result['ssh_probe']['status'] == 'REMOTE_COMMAND_PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
