"""Regression: service startup must never start scheduled paid pattern reviews."""
import sys
from types import SimpleNamespace
import web_app

def test_service_startup_does_not_start_six_hour_ai_review(monkeypatch):
    started=[];served=[]
    class StartupThread:
        def __init__(self,*,target,name,daemon):
            self.name=name
        def start(self):
            started.append(self.name)
    monkeypatch.setattr(web_app, '_ensure_server_logging', lambda:None)
    monkeypatch.setattr(web_app.threading, 'Thread', StartupThread)
    def forbidden(*args,**kwargs):
        raise AssertionError('scheduled paid AI pattern review started')
    monkeypatch.setattr(web_app.trade_learning_scheduler, 'start_review_scheduler', forbidden)
    monkeypatch.setitem(sys.modules,'waitress',SimpleNamespace(serve=lambda *args,**kwargs:served.append(kwargs)))
    web_app.main()
    assert served and served[0]['port']==int(web_app.os.environ.get('PORT','8080'))
    assert started==['deployment-resume']
