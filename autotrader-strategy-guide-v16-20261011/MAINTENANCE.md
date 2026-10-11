# Strategy and operations guide maintenance

The dashboard footer is driven by strategy_guide.build(UserConfig) for server-rendered HTML and existing /api/state refresh. No additional API/AI/exchange request. Amounts, modes, enabled symbols,intervals, losslimits and CORE reduction bounds come from current settings/production policy. JS uses textContent and skips unchanged payloads,preserving open details.

Every change to trading authority,entry/exit conditions,profit protection,additional exposure,APIcost scheduling or execution restrictions must update the matching prose and REVISION in strategy_guide.py in the same release. Run tests/test_strategy_guide_v16.py and verify /api/state.strategy_guide against actual live config. This is release documentation,not a strategy engine. Backend operations/roles remain unchanged.

Guide dates indicate explanation revision and deployed code date. Config values refresh automatically. Commentary/wording cannot be automatically inferred from arbitrary future source edits; the release maintainer must update behavior explanations explicitly.
