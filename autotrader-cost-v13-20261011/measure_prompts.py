"""Offline provider-boundary capture; synthetic same facts, no external API."""
import inspect
import json
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import gemini_analyzer as gemini
import openai_analyzer as gpt
import core_entry_timing
import position_management_context
from adaptive_exit_engine import build_ai_price_contract,format_ai_price_contract
from adaptive_exit_policy import production_adaptive_exit_policy

position=dict(side='long',contracts=10.,entry_price=.2541,mark_price=.259,
              unrealized_pnl=.049,position_id='p1',entry_timestamp_ms=1)
protection=dict(algo_id='oco1',sl_price=.244,tp_price=.28,sz=10.)
review=dict(assessment='thesis_intact',confidence=.85,reasoning='confirmed trend; protect observed giveback',
            risk_level='medium',suggested_reduce_fraction=.15)
payload=dict(action='hold',confidence=.85,reasoning='retain observed trend',market_regime='bullish',
             regime_confidence=.85,trade_alignment='with_regime',exit_plan=None,position_review=review)
facts='\n'.join(f'{tf}: last=.259 open=.258 high=.260 low=.257 volume=12000 ema20=.257 ema50=.255 rsi14=58 macd=.0004 atr14=.002; 60m high=.260 low=.253 pct=1.5; structure=HH/HL' for tf in ['1m','3m','5m','15m','1h','4h','1d'])
facts+='\nCORE_ENTRY_TIMING status=ok phase=established side=long origin_closed_at=2026-10-11T04:00:00+09:00 age_minutes=25 move_from_origin_atr=1.4 setup_kind=first_range_break'
facts+='\nMARKET_CONTEXT status=unknown as_of=2026-10-11T04:25:00+09:00; FOMC/event unavailable, not neutral'
evidence=position_management_context.render(dict(mfe_known=True,mfe_r=1.4,current_r=.8,giveback_r=.6,
    reduction_known=True,initial_contracts=10,remaining_contracts=10,cumulative_reduced_ratio=0,
    partial_reduction_remaining_fraction=.5,pending_quantity_change=False,evidence_bar_time='2026-10-11T04:24:00+09:00'))
contracts={side:format_ai_price_contract(build_ai_price_contract(side,.259,.002,production_adaptive_exit_policy(),leverage=5,estimated_roundtrip_cost_rate=.001)) for side in ['long','short']}
merged='protection' in inspect.signature(gemini.analyze).parameters
summary=facts+'\n'+('' if merged else contracts['long'])+'\n'+contracts['short']+evidence
prompts=[]
def generate(cfg,symbol,purpose,**kwargs):
    prompts.append((purpose,kwargs['contents']))
    return SimpleNamespace(text=json.dumps(payload if purpose=='core_primary_decision' else review),usage_metadata=None)
gemini._get_client=lambda _:object();gemini._generate_content_observed=generate
def create(**kwargs):
    prompts.append(('gpt_management',kwargs['messages'][0]['content']))
    return SimpleNamespace(usage=None,choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(dict(action='HOLD',confidence=.9,reasoning='retain'))))])
gpt._ensure_response_models_warmed_up=lambda:None
gpt._get_client=lambda _:SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
with tempfile.TemporaryDirectory() as temp:
    cfg=SimpleNamespace(user_dir=temp,logger=logging.getLogger('measure'),CORE_AI_STRATEGY_AUTHORITY=True,
        RISK_ADAPTIVE_PARTIAL_ENABLED=True,GEMINI_MODEL='offline',OPENAI_MODEL='offline',OPENAI_API_KEY='offline',MIN_CONFIDENCE=.6)
    decision=gemini.analyze(cfg,'ADA/USDT:USDT',['1m','3m','5m','15m','1h','4h','1d'],summary,position,**({'protection':protection} if merged else {}))
    held_summary=summary if merged else summary+evidence
    held=review if merged else gemini.analyze_held_position(cfg,'ADA/USDT:USDT',['1m','3m','5m','15m','1h','4h','1d'],held_summary,position,decision,protection=protection)
    gpt.verify_position_management(cfg,'ADA/USDT:USDT',['1m','3m','5m','15m','1h','4h','1d'],held_summary,position,decision,held,protection=protection)
result={'sample':'synthetic same seven-TF evidence, matched management due',
        'paid_calls':0,'provider_requests':len(prompts),'input_chars':sum(len(p) for _,p in prompts),
        'by_stage':{s:len(p) for s,p in prompts},'schema_and_output_tokens_included':False}
try:
    import tiktoken
    enc=tiktoken.get_encoding('o200k_base')
    result['tokenizer_proxy']='o200k_base (not Gemini billing tokenizer)'
    result['proxy_input_tokens']=sum(len(enc.encode(p)) for _,p in prompts)
except ImportError:
    result['tokenizer_proxy']=None
print(json.dumps(result))
