"""Per-account primary provider; GPT_ONLY never invokes Gemini SDK."""
import gemini_analyzer as _legacy
import gpt_primary_v33_97 as _gpt

def _pick(cfg):
    return _gpt if getattr(cfg,"GPT_PRIMARY_ONLY",False) else _legacy

def analyze(cfg,*args,**kwargs):
    return _pick(cfg).analyze(cfg,*args,**kwargs)

def analyze_held_position(cfg,*args,**kwargs):
    return _pick(cfg).analyze_held_position(cfg,*args,**kwargs)

def propose_entry_exit_plan(cfg,*args,**kwargs):
    return _pick(cfg).propose_entry_exit_plan(cfg,*args,**kwargs)

def review_strategy_report(cfg,*args,**kwargs):
    return _pick(cfg).review_strategy_report(cfg,*args,**kwargs)

def parse_adaptive_exit_assessment(raw):
    return _gpt.parse_adaptive_exit_assessment(raw)
