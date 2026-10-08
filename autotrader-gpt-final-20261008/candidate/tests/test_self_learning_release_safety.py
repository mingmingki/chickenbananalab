import os
from pathlib import Path

from scripts.build_self_learning_release import build_release_candidate


def test_release_builder_never_dereferences_users(tmp_path):
    shared=tmp_path/'shared-users'; shared.mkdir(); (shared/'DO_NOT_COPY').write_text('secret')
    src=tmp_path/'src'; src.mkdir(); (src/'app.py').write_text('x=1')
    (src/'users').symlink_to(shared, target_is_directory=True)
    dest=tmp_path/'dest'
    build_release_candidate(str(src),str(dest),'/var/lib/autotrader/users')
    assert (dest/'app.py').read_text()=='x=1'
    assert (dest/'users').is_symlink()
    assert os.readlink(dest/'users')=='/var/lib/autotrader/users'
    assert not (dest/'DO_NOT_COPY').exists()
    assert not (dest/'users'/'DO_NOT_COPY').exists()  # target is intentionally not mounted in test


def test_release_builder_refuses_existing_destination(tmp_path):
    src=tmp_path/'src'; src.mkdir(); (src/'app.py').write_text('x')
    dest=tmp_path/'dest'; dest.mkdir()
    try:
        build_release_candidate(str(src),str(dest),'/var/lib/autotrader/users')
    except FileExistsError:
        pass
    else:
        raise AssertionError('existing destination must be refused')
