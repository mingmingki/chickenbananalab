from pathlib import Path


def test_autotrader_nginx_gzip_config_compresses_json_and_varies():
    path = Path(__file__).resolve().parents[1] / 'nginx-json-gzip.conf'
    assert path.exists()
    text = path.read_text(encoding='utf-8')
    assert not any(line.strip() == 'gzip on;' for line in text.splitlines())
    assert 'gzip_vary on;' in text
    assert 'gzip_min_length 1024;' in text
    assert 'application/json' in text
    assert 'application/javascript' in text
    assert 'text/plain' in text
