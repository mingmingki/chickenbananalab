"""Patch an isolated exact v33-111 source tree, never a running release."""
import ast
from pathlib import Path
import sys

root=Path(sys.argv[1])


def edit(name,old,new):
    path=root/name
    text=path.read_text()
    assert text.count(old)==1,(name,text.count(old),old[:90])
    result=text.replace(old,new,1)
    ast.parse(result,filename=name)
    path.write_text(result)


helper='''def _observe_core_ai_plan_shadow(cfg, symbol, *, event, frames=None, now=None,
                                 decision=None, position=None, protection=None,
                                 receipt=None, gates=None, context=None):
    """Observational boundary only: no client, no return value used for orders."""
    try:
        if (symbol not in config.CORE_SYMBOLS or not strategy_authority.core_ai(cfg)):
            return
        from pathlib import Path
        if not (Path(cfg.user_dir)/'core_ai_plan_shadow_v33_112.enabled').is_file():
            return
        import core_ai_plan_shadow_v33_112 as plan_shadow
        opening = trade_log.last_unclosed_open(cfg.user_dir, symbol) if position else None
        context = dict(context or {})
        if position:
            reduction = reduce_v2_state.get(cfg.user_dir, symbol) or {}
            identity = reduce_v2_state.position_identity(position)
            if (identity and reduction.get('lifecycle_id') == identity
                    and reduction.get('baseline_known') is True):
                context.update(initial_contracts=reduction.get('initial_contracts'),
                               initial_contracts_verified=True)
        frame = frames.get('5m') if isinstance(frames, dict) else None
        result = plan_shadow.capture(
            cfg, symbol, config.CORE_SYMBOLS, event=event,
            now=now or datetime.datetime.now(datetime.timezone.utc), bars=frame,
            decision=decision, position=position, opening=opening,
            protection=protection, receipt=receipt, gates=gates, context=context)
        if result.get('status') == 'unavailable':
            cfg.logger.warning('[%s] CORE_AI_PLAN_SHADOW_V33_112_UNAVAILABLE error=%s',
                               symbol, result.get('error'))
    except Exception:
        cfg.logger.warning('[%s] CORE_AI_PLAN_SHADOW_V33_112_UNAVAILABLE',
                           symbol, exc_info=True)


'''
edit('trader.py','def _management_review_frames(cfg, client, raw_dfs):',
     helper+'def _management_review_frames(cfg, client, raw_dfs):')
edit('trader.py',
     '    position_ai_log.record_event(\n        cfg.user_dir, "gemini_position_review", symbol, review_id,',
     '''    _observe_core_ai_plan_shadow(
        cfg, symbol, event='held_ai_review', frames=raw_dfs,
        decision=gemini_review, position=position, protection=protection, receipt=review_id,
        context=dict(review_path=review_path, decision_regime=decision.get('market_regime'),
                     market_asof=decision.get('_candle_asof')))
    position_ai_log.record_event(
        cfg.user_dir, "gemini_position_review", symbol, review_id,''')
edit('trader.py',
     '    display_action = (f"REDUCE_INITIAL_{gpt_result.get(\'reduce_fraction\')}" if adaptive_reduction.enabled(cfg)',
     '''    _observe_core_ai_plan_shadow(
        cfg, symbol, event='management_decision', frames=raw_dfs,
        decision=gpt_result, position=position, protection=protection, receipt=review_id,
        context=dict(review_path=review_path, decision_regime=decision.get('market_regime'),
                     market_asof=decision.get('_candle_asof'),
                     fraction_basis='initial' if adaptive_reduction.enabled(cfg)
                                    and strategy_authority.core_ai(cfg) else None))
    display_action = (f"REDUCE_INITIAL_{gpt_result.get('reduce_fraction')}" if adaptive_reduction.enabled(cfg)''')
edit('trader.py',
     '        # Passive two-week experiment: no orders, no protection changes, no AI action.',
     '''        _observe_core_ai_plan_shadow(
            cfg, symbol, event='market_tick', frames=closed_dfs,
            now=market_candle_asof)

        # Passive two-week experiment: no orders, no protection changes, no AI action.''')
edit('trader.py',
     "    if prefilter['skip_ai']:\n        previous_skip =",
     """    if prefilter['skip_ai']:
        _observe_core_ai_plan_shadow(
            cfg, symbol, event='prefilter_skip', now=market_candle_asof,
            gates=prefilter.get('gates'), context=dict(trigger=ai_budget['reason'],
                                                    ai_not_evaluated_reason=prefilter['reason']))
        previous_skip =""")
edit('trader.py',
     '    decision["_entry_timing"] = entry_timing',
     '''    _observe_core_ai_plan_shadow(
        cfg, symbol, event='primary_decision', frames=closed_dfs,
        decision=decision, position=position, protection=primary_protection,
        gates=prefilter.get('gates'),
        context=dict(market_asof=market_candle_asof.isoformat(),
                     trigger=ai_budget['reason'],
                     snapshot_id=market_snapshot.get('snapshot_id') if market_snapshot else None))
    decision["_entry_timing"] = entry_timing''')

route='''@app.route('/api/ops/core-ai-plan-shadow-v33-112', methods=['GET'])
@login_required
def api_core_ai_plan_shadow_v33_112():
    """Session-scoped, read-only research evidence; never a promotion endpoint."""
    import core_ai_plan_shadow_v33_112 as plan_shadow
    ctx = get_context(session['username'])
    report = plan_shadow.read_snapshot(ctx.dir, details=request.args.get('details') == '1')
    return jsonify(ok=report['status'] != 'unavailable', report=report,
                   execution_permission=False), (503 if report['status'] == 'unavailable' else 200)


'''
edit('web_app.py',"@app.route('/api/ops/profitability-v33-101', methods=['GET'])",
     route+"@app.route('/api/ops/profitability-v33-101', methods=['GET'])")
print('V33_112_OBSERVATIONAL_HOOKS_PATCHED')
