#!/usr/bin/env python3
"""Run public-data ablations; this script cannot create exchange orders."""
import argparse
import csv
import datetime as dt
import hashlib
import itertools
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core_trend_replay_v33_109 import build_signals,simulate,summarize,combine_curves,QUARTER


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    start=int(dt.datetime(2024,10,1,tzinfo=dt.timezone.utc).timestamp()*1000)
    universe={}
    sources=[]
    for symbol in ['BTC','ETH','XRP','ADA','SOL','DOGE']:
        files=[args.data/f'{symbol}-USDT-SWAP_{tf}.json' for tf in ['4H','15m']]
        four,execution=[json.loads(p.read_text()) for p in files]
        universe[symbol]=(execution,build_signals(four))
        for p,rows in zip(files,[four,execution]):
            sources.append(dict(file=p.name,rows=len(rows),first_ms=int(rows[0][0]),last_ms=int(rows[-1][0]),
                                sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    variants={}
    for stop,onset,exit_mode in itertools.product([1.,2.],[False,True],['tp2r','ema50']):
        name=f'sl{stop:g}_onset{int(onset)}_{exit_mode}_ideal'
        variants[name]=dict(stop_atr=stop,onset_only=onset,exit_mode=exit_mode,delay_ms=0,slippage=0.)
    variants['target_delay15_slippage']=dict(stop_atr=2.,onset_only=True,exit_mode='ema50',delay_ms=QUARTER,slippage=.0005)
    variants['target_delay15_funding_stress']=dict(variants['target_delay15_slippage'],funding_rate_8h=.0001)
    for stop,ema in itertools.product([1.5,2.,2.5,3.],['ema20','ema50']):
        variants[f'sensitivity_sl{stop:g}_{ema}']=dict(stop_atr=stop,onset_only=True,exit_mode=ema,delay_ms=0,slippage=0.)
    output=dict(label='RETROSPECTIVE_RULE_PROXIES_NOT_GPT_REPLAY',start_ms=start,sources=sources,
                assumptions=dict(fee_per_leg=.0005,ideal_delay_ms=0,paper_delay_ms=QUARTER,
                funding='excluded except explicitly labelled adverse 0.01%/8h stress; not historical actual funding',
                risk_curve='sum of per-trade price-risk R at 15m marks; no compounding/account return claim',
                terminal_positions='marked with estimated exit costs; not counted as strategy exits'),variants={})
    chosen=None
    for name,policy in variants.items():
        results={s:simulate(bars,signals,start_ms=start,**policy) for s,(bars,signals) in universe.items()}
        output['variants'][name]=dict(policy=policy,**summarize(results))
        print(name,json.dumps({k:output['variants'][name][k] for k in ['closed_trades','closed_net_r','terminal_mark_net_r','max_drawdown_r_15m_mark']}),flush=True)
        if name=='target_delay15_slippage':chosen=results
    (args.output/'summary.json').write_text(json.dumps(output,indent=2,allow_nan=False))
    trades=[dict(t,symbol=s) for s,r in chosen.items() for t in r['trades']]
    trades.sort(key=lambda t:(t['exit_ms'],t['symbol']))
    with (args.output/'target_trades.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(trades[0]) if trades else ['symbol'])
        writer.writeheader();writer.writerows(trades)
    curve=combine_curves({s:r['curve'] for s,r in chosen.items()})['curve']
    with (args.output/'target_portfolio_r_curve.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['timestamp_ms','net_r_mark'])
        writer.writerows(curve)
    print('REPLAY_DONE',args.output,flush=True)


if __name__=='__main__':main()
