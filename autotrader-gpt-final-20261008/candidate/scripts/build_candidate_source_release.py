"""Explicit source-only ZIP; never deploys, imports the application, or reads credentials."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

# Reviewed production modules and public assets. Runtime state is not discovered
# by globbing. Any new local import missing here blocks the build.
SOURCE_PATHS = tuple('''accounts.py app_window.py backtest_engine.py bithumb_client.py
bithumb_correlation.py candidate_c_backtest_signal_adapter.py candidate_c_cycle_reconciliation.py
candidate_c_decision_engine.py candidate_c_exit_management.py candidate_c_forward_paper_trading.py
candidate_c_gpt_gate_adapter.py
candidate_c_gpt_gate_log.py candidate_c_hybrid_bars.py candidate_c_hybrid_cycle.py
candidate_c_hybrid_live_adapter.py candidate_c_hybrid_ownership.py candidate_c_intent_ledger.py
candidate_c_indicator_contract.py candidate_c_live_activation.py candidate_c_manual_close.py
candidate_c_position_reconciliation.py candidate_c_reversal_state_machine.py candidate_c_runtime.py
candidate_c_setup_tracker.py candidate_c_strategy_policy.py candidate_c_timeframe_contract.py
candidate_c_trader_adapter.py candle_finality.py candle_finality_log.py capital_flow.py config.py core_add_position_state.py
core_kill_switch.py core_long_confirmation.py core_manual_close.py core_reduce_fill_accounting.py core_short_downgrade.py
core_short_level.py core_tactical_short.py cost_accounting.py derived_candles.py entry_overextension_guard.py entry_veto_outcome.py
entry_veto_shadow.py entry_veto_shadow_log.py exchange_fee_ledger.py okx_margin_return.py execution_units.py fast_trade_history.py fx_rate.py
gemini_analyzer.py gpt_hold_audit.py gpt_latency_log.py gpt_shadow_log.py indicators.py jsonl_cache.py
market_data_store.py market_structure.py market_structure_log.py mtf_asof.py okx_client.py
openai_analyzer.py order_safety.py pnl_reconciliation.py pnl_store.py portfolio_mtm_engine.py
position_ai_log.py process_lock.py reduce_v2_state.py regime_classifier.py regime_shadow_log.py
requirements.txt risk_manager.py setup.py shadow_positions.py state.py stop_contract.py
strategy_indicators.py strategy_version.py symbol_entry_control.py telegram_notify.py timeframes.py
trade_log.py trader.py usage_log.py web_app.py web_push.py
static/img/chicken_cert.png static/img/favicon-32.png static/img/icon-192.png static/img/icon-512.png
static/img/icon-maskable-512.png static/manifest.json static/sw.js
templates/admin.html templates/dashboard.html templates/guide.html templates/login.html templates/signup.html
scripts/build_candidate_source_release.py scripts/diagnose_candidate_access.py
scripts/plan_candidate_deployment.py'''.split())
MANIFEST = 'SOURCE_MANIFEST.json'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _validate_name(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or path.is_absolute() or '..' in path.parts
            or str(path) != name or any(part.startswith('.') for part in path.parts)):
        raise ValueError('forbidden_source_path')
    allowed = ((len(path.parts) == 1 and (path.suffix == '.py' or name == 'requirements.txt'))
               or (path.parts[0] == 'templates' and path.suffix == '.html')
               or (path.parts[0] == 'static' and (path.suffix in ('.png', '.js', '.css', '.svg')
                                                or name == 'static/manifest.json'))
               or (path.parts[0] == 'scripts' and name in SOURCE_PATHS))
    if not allowed:
        raise ValueError('forbidden_source_path:' + name)


def _read_source(root, name):
    _validate_name(name)
    path = root
    for component in PurePosixPath(name).parts:
        path = path / component
        if path.is_symlink():
            raise ValueError('symlink_source:' + name)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError('nonregular_or_hardlinked_source:' + name)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as handle:
        return handle.read()


def _check_local_imports(root, contents):
    for name, data in contents.items():
        if not name.endswith('.py'):
            continue
        for node in ast.walk(ast.parse(data, filename=name)):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for imported in names:
                module = imported.split('.')[0] + '.py'
                if (root / module).is_file() and module not in contents:
                    raise ValueError('missing_local_import:' + module + ':from:' + name)


def build_release(root, output_dir, *, paths=SOURCE_PATHS):
    root, output_dir = Path(root).resolve(), Path(output_dir).absolute()
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError('output_must_be_new_directory_outside_source')
    output_dir = output_dir.resolve()
    if root == output_dir or root in output_dir.parents:
        raise ValueError('output_must_be_new_directory_outside_source')
    if len(set(paths)) != len(paths) or not paths:
        raise ValueError('empty_or_duplicate_source_allowlist')
    contents = {name: _read_source(root, name) for name in sorted(paths)}
    _check_local_imports(root, contents)
    files = [{'path': name, 'sha256': sha256(data), 'size': len(data)} for name, data in contents.items()]
    manifest = {'schema': 1, 'artifact_type': 'source_only_not_deployable_without_vm_evidence',
                'files': files, 'includes_credentials': False, 'includes_runtime_state': False,
                'dependency_environment': 'not_bundled_or_validated_by_source_packager'}
    encoded = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    digest = sha256(encoded)
    release_id = 'candidate-c-' + digest[:16]
    # Reject a source edit during collection. The ZIP always uses the exact
    # captured bytes; a later change requires a new artifact, never in-place edits.
    if any(_read_source(root, name) != data for name, data in contents.items()):
        raise ValueError('source_changed_during_snapshot')
    output_dir.mkdir(parents=False)
    archive_path = output_dir / (release_id + '.zip')
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted({**contents, MANIFEST: encoded}.items()):
            item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, data)
    verify_release(archive_path)
    (output_dir / MANIFEST).write_bytes(encoded)
    sums = ''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in files)
    (output_dir / 'SHA256SUMS').write_text(sums, encoding='utf-8')
    result = {'release_id': release_id, 'archive': str(archive_path), 'file_count': len(files),
              'archive_sha256': sha256(archive_path.read_bytes()),
              'source_manifest_sha256': digest, 'deployment_status': 'NOT_DEPLOYED'}
    (output_dir / 'ARTIFACT.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def verify_release(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or MANIFEST not in names:
            raise ValueError('invalid_archive_members')
        if any(not stat.S_ISREG(item.external_attr >> 16) for item in archive.infolist()):
            raise ValueError('nonregular_archive_member')
        manifest = json.loads(archive.read(MANIFEST))
        if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), list):
            raise ValueError('invalid_source_manifest')
        expected = {MANIFEST}
        for row in manifest['files']:
            name = row['path']
            _validate_name(name)
            if name in expected:
                raise ValueError('duplicate_manifest_member')
            expected.add(name)
            data = archive.read(name)
            if len(data) != row['size'] or sha256(data) != row['sha256']:
                raise ValueError('source_hash_mismatch:' + name)
        if expected != set(names):
            raise ValueError('unexpected_archive_member')
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        manifest = verify_release(args.verify)
        print(json.dumps({'verified': True, 'files': len(manifest['files'])}))
    else:
        if not args.root or not args.output_dir:
            parser.error('--root and --output-dir are required when building')
        print(json.dumps(build_release(args.root, args.output_dir), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
