"""Generate reviewable argv phases only; never execute a deployment or SSH.

The input must reference fresh actual VM evidence and verified operational
hooks. This validator checks completeness/consistency, not the truth of operator
attestations. The mandatory preflight hook must recheck live facts before stop.
"""
import argparse
import datetime
import json
from pathlib import Path, PurePosixPath
import re

HASH = re.compile(r'[a-f0-9]{64}')
PATH = re.compile(r'/[A-Za-z0-9_./-]+')
CHECK_LINK = ('import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
              'sys.exit(0 if p.is_symlink() and str(p.resolve(strict=True))==sys.argv[2] else 1)')
CHECK_HASH = ('import hashlib,pathlib,sys; '
              'sys.exit(0 if hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2] else 1)')
CHECK_SOURCE = '''import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve(strict=True)
raw=pathlib.Path(sys.argv[3]).read_bytes()
if hashlib.sha256(raw).hexdigest()!=sys.argv[2]: raise SystemExit('manifest_hash_mismatch')
manifest=json.loads(raw)
if manifest['schema']!=1: raise SystemExit('manifest_schema_mismatch')
for item in manifest['files']:
 p=root/item['path']
 if p.is_symlink() or root not in p.resolve(strict=True).parents: raise SystemExit('source_path_mismatch')
 data=p.read_bytes()
 if len(data)!=item['size'] or hashlib.sha256(data).hexdigest()!=item['sha256']: raise SystemExit('source_hash_mismatch')
'''


def _path(value):
    if (not isinstance(value, str) or not PATH.fullmatch(value)
            or value == '/' or '..' in PurePosixPath(value).parts
            or str(PurePosixPath(value)) != value):
        raise ValueError('explicit_normalized_absolute_path_required')
    return value


def _step(argv, *, must_pass=True):
    return {'argv': argv, 'must_pass_before_next_step': must_pass}


def build_plan(evidence, *, now=None):
    if not isinstance(evidence, dict):
        raise ValueError('vm_evidence_object_required')
    required = ('observed_at_utc host service current_link previous_release new_release persistent_root '
                'current_manifest_path new_manifest_path '
                'current_manifest_sha256 new_manifest_sha256 remote_source_diff_report_sha256 '
                'schema_compatibility_report_sha256 exchange_reconciliation_report_sha256 memory_report_sha256 '
                'memory_high memory_max running_loops resume_spec_path resume_spec_sha256 '
                'restore_hook restore_hook_sha256 health_hook health_hook_sha256 persistent_links '
                'unknown_orders positions_and_protection_reconciled shared_source_diff_reviewed '
                'schema_rollback_compatible restore_prior_running_only_verified').split()
    missing = [key for key in required if key not in evidence]
    if missing:
        raise ValueError('missing_vm_evidence:' + ','.join(missing))
    for key in required:
        if key.endswith('_sha256') and (not isinstance(evidence[key], str) or not HASH.fullmatch(evidence[key])):
            raise ValueError('evidence_digest_required:' + key)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    observed = datetime.datetime.fromisoformat(evidence['observed_at_utc'])
    if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= 300:
        raise ValueError('fresh_vm_evidence_required')
    if not re.fullmatch(r'[A-Za-z0-9_-]+@[A-Za-z0-9.-]+', evidence['host']):
        raise ValueError('explicit_verified_ssh_target_required')
    service = evidence['service']
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', service):
        raise ValueError('invalid_service')
    for key in ('current_link', 'previous_release', 'new_release', 'persistent_root',
                'current_manifest_path', 'new_manifest_path',
                'resume_spec_path', 'restore_hook', 'health_hook'):
        _path(evidence[key])
    current, previous, new, persistent = (evidence[key] for key in
                                         ('current_link', 'previous_release', 'new_release', 'persistent_root'))
    roots = [PurePosixPath(value) for value in (current, previous, new, persistent)]
    if len(set(roots)) != 4 or any(a in b.parents for a in roots for b in roots if a != b):
        raise ValueError('code_state_paths_must_not_overlap')
    if evidence['unknown_orders'] is not False:
        raise ValueError('unknown_orders_block_cutover')
    for key in ('positions_and_protection_reconciled', 'shared_source_diff_reviewed',
                'schema_rollback_compatible', 'restore_prior_running_only_verified'):
        if evidence[key] is not True:
            raise ValueError('unverified_cutover_condition:' + key)
    for key in ('memory_high', 'memory_max'):
        value = evidence[key]
        if not isinstance(value, str) or (value != 'max' and not value.isdigit()):
            raise ValueError('exact_observed_memory_limit_required')
    loops = evidence['running_loops']
    if not isinstance(loops, list):
        raise ValueError('exact_previous_running_loops_required')
    seen = set()
    for loop in loops:
        if (not isinstance(loop, dict) or not re.fullmatch(r'[A-Za-z0-9_]{3,20}', loop.get('user', ''))
                or loop.get('group') not in ('core', 'candidate_c')
                or not isinstance(loop.get('symbols'), list) or not loop['symbols']):
            raise ValueError('invalid_loop_snapshot')
        identity = (loop['user'], loop['group'])
        if identity in seen or len(set(loop['symbols'])) != len(loop['symbols']):
            raise ValueError('duplicate_loop_snapshot')
        seen.add(identity)
        if any(not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]+/USDT:USDT', symbol)
               for symbol in loop['symbols']):
            raise ValueError('invalid_loop_symbol')
    links = evidence['persistent_links']
    if not isinstance(links, dict) or not {'users', 'logs'}.issubset(links):
        raise ValueError('complete_persistent_link_manifest_required')
    for name, target in links.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or str(relative) != name:
            raise ValueError('invalid_persistent_link_name')
        _path(target)
        if PurePosixPath(persistent) not in PurePosixPath(target).parents:
            raise ValueError('persistent_link_outside_verified_state_root')

    health_base = [evidence['health_hook'], '--resume-spec', evidence['resume_spec_path'],
                   '--expected-spec-sha256', evidence['resume_spec_sha256'],
                   '--persistent-root', persistent, '--memory-high', evidence['memory_high'],
                   '--memory-max', evidence['memory_max']]
    restore = [evidence['restore_hook'], '--resume-spec', evidence['resume_spec_path'],
               '--expected-spec-sha256', evidence['resume_spec_sha256'], '--previous-running-only']
    preflight = [_step(['systemctl', 'show', service,
                        '--property=ActiveState,SubState,MainPID,User,Group,WorkingDirectory,ControlGroup,MemoryCurrent,MemoryHigh,MemoryMax,MemorySwapCurrent,MemorySwapMax']),
                 _step(['python3', '-c', CHECK_LINK, current, previous])]
    for release, digest, manifest_path in (
            (previous, evidence['current_manifest_sha256'], evidence['current_manifest_path']),
            (new, evidence['new_manifest_sha256'], evidence['new_manifest_path'])):
        preflight.append(_step(['python3', '-c', CHECK_SOURCE, release, digest, manifest_path]))
        for name, target in sorted(links.items()):
            preflight.append(_step(['python3', '-c', CHECK_LINK, release + '/' + name, target]))
    for path_key, digest_key in (('resume_spec_path', 'resume_spec_sha256'),
                                 ('restore_hook', 'restore_hook_sha256'), ('health_hook', 'health_hook_sha256')):
        preflight.append(_step(['python3', '-c', CHECK_HASH, evidence[path_key], evidence[digest_key]]))
    preflight.append(_step(health_base + ['--phase', 'preflight', '--expected-release', previous]))

    def switch_phase(target, tag):
        temporary = current + '.' + tag + '-' + evidence['new_manifest_sha256'][:12]
        return [_step(['test', '!', '-e', temporary]), _step(['test', '!', '-L', temporary]),
                _step(['systemctl', 'stop', service]),
                _step(['ln', '-s', target, temporary]), _step(['mv', '-T', temporary, current]),
                _step(['systemctl', 'start', service]), _step(restore),
                _step(health_base + ['--phase', tag, '--expected-release', target])]

    return {'schema': 1, 'execution_performed': False, 'host': evidence['host'],
            'evidence_observed_at_utc': evidence['observed_at_utc'],
            'running_loops_to_restore': loops,
            'preflight': preflight, 'cutover': switch_phase(new, 'cutover'),
            'rollback': switch_phase(previous, 'rollback'),
            'failure_policy': 'Stop on any preflight failure; after cutover failure use the separately verified rollback phase. Never restore financial state snapshots.',
            'hook_requirement': 'Verified hooks must enforce fresh exchange/order/loop/source/schema/memory evidence. This planner does not supply or execute them.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = build_plan(json.loads(args.evidence.read_text()))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'deployment_status': 'BLOCKED', 'reason': str(exc)}))
        return 2
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
