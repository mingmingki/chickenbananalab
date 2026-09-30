import json
from pathlib import Path
import pytest

import learning_control


def test_absent_or_corrupt_control_defaults_off(tmp_path):
    assert learning_control.get(str(tmp_path))['live_enabled'] is False
    (tmp_path/'self_learning_control.json').write_text('{bad')
    assert learning_control.get(str(tmp_path))['live_enabled'] is False


def test_set_live_enabled_is_atomic_and_persistent(tmp_path):
    row = learning_control.set_live_enabled(str(tmp_path), True)
    assert row['live_enabled'] is True
    assert learning_control.get(str(tmp_path))['live_enabled'] is True
    data=json.loads((tmp_path/'self_learning_control.json').read_text())
    assert data['live_enabled'] is True
    assert not list(tmp_path.glob('.self_learning_control.*'))


def test_control_rejects_non_boolean(tmp_path):
    for value in (1,0,'true',None,[],{}):
        with pytest.raises(ValueError, match='boolean_required'):
            learning_control.set_live_enabled(str(tmp_path), value)
