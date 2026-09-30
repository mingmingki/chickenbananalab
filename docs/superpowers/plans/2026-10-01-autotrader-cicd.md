# Autotrader CI/CD Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement inline.

**Goal:** Prepare, validate and publish the transition branch, then verify Actions SSH connectivity without affecting trading.
**Architecture:** Allowlisted source import, isolated offline tests, gated artifact, read-only SSH probe.
**Tech Stack:** Python 3.11, pytest, GitHub Actions, OpenSSH, GCP Compute Engine.
**Spec:** docs/superpowers/specs/2026-10-01-autotrader-cicd-design.md

## Global Constraints
- Never upload secrets, accounts, logs or trading data.
- Never operate trading APIs, runtime configuration or live services.
- Fail closed on validation failures and missing connection configuration.
- Preserve the original workspace and branch history.

## Review Focus
- Symlinks and runtime files must not enter artifacts.
- Missing test modules must fail CI, not be silently skipped.
- Tests must not inherit keys or read the live project directory.
- SSH must verify the host key and fail on authentication errors.
- A failed regression must prevent artifact publication; a failed safety check must also block the read-only probe.

## Tasks
- [x] Import source only; inventory omissions and preserve original bytes.
- [x] Test packaging and offline runner failures, then implement guards.
- [x] Configure CI, separate historical suites and read-only connection job.
- [x] Run all suites; record existing failures without weakening assertions or trading behavior.
- [ ] Review, commit and push the transition branch; inspect Actions results.

## Execution record
- Root workspace is main; local transition branch exists at 37cc7f8 with no CI changes.
- Remote transition branch is absent. Direct Git network cannot resolve github.com.
- Original .git is read-only; isolated local clone created under /private/tmp.
- DC denied by approval policy. GitHub connector reads work; writes not yet attempted.
- User instruction to continue overrides intermediate skill approval pauses.

- Ruling: preserve application source and test assertions; 25 pre-existing regression failures block release publication, but not the read-only transport check. No trading behavior is changed.
- Independent review: no blocking findings; 13 infrastructure tests pass. Python network guard is accidental-I/O prevention, not an OS sandbox.
