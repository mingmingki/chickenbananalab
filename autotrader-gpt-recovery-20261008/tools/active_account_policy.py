"""Change only three explicitly requested settings on an identified account.

Does not identify/guess the active account, start loops, or send trades. Use only
after the current VM account and actual release have been audited and qualified.
"""
import argparse
import os
from pathlib import Path
import tempfile

SETTINGS={'GPT_ENTRY_GATE_ENABLED':'true','CORE_GPT_ENTRY_TIMEOUT_BYPASS':'true',
          'CORE_PAID_SHADOW_ENABLED':'false'}


def updated(text):
    result=[];seen=set()
    for line in text.splitlines():
        key=line.partition('=')[0].strip()
        if key in SETTINGS and not line.lstrip().startswith('#'):
            if key not in seen: result.append(key+'='+SETTINGS[key])
            seen.add(key)
        else:
            result.append(line)
    result.extend(key+'='+value for key,value in SETTINGS.items() if key not in seen)
    return '\n'.join(result)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account_dir',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    path=args.account_dir/'.env'
    text=path.read_text()
    result=updated(text)
    if args.apply:
        backup=path.with_name('.env.before-core-gpt-recovery-20261008')
        if backup.exists(): raise SystemExit('backup already exists: audit before resuming')
        backup.write_text(text);backup.chmod(0o600)
        fd,name=tempfile.mkstemp(prefix='.env.core-gpt-',dir=path.parent)
        with os.fdopen(fd,'w') as f:
            f.write(result);f.flush();os.fsync(f.fileno())
        os.chmod(name,0o600);os.replace(name,path)
    print({'changed':result!=text,'applied':args.apply,'settings':SETTINGS})


if __name__=='__main__': main()
