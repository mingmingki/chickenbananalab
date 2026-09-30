"""Build a release candidate without ever dereferencing the shared users link."""
from __future__ import annotations

import os
import shutil


def _ignore(_dir, names):
    ignored=[]
    for name in names:
        if name == 'users' or name.startswith('users.misbound_backup_'):
            ignored.append(name)
    return ignored


def build_release_candidate(source: str, destination: str, users_target: str = '/var/lib/autotrader/users') -> str:
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    source_real=os.path.realpath(source)
    shutil.copytree(source_real, destination, symlinks=True, ignore=_ignore)
    users_path=os.path.join(destination,'users')
    os.symlink(users_target, users_path, target_is_directory=True)
    if not os.path.islink(users_path) or os.readlink(users_path) != users_target:
        raise RuntimeError('users_symlink_guard_failed')
    return destination
