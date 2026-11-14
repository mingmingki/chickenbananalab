#!/usr/bin/env bash
# Chickenbanana autotrader - READ-ONLY audit evidence collector.
#
# Run on the autotrader VM:
#   sudo bash collect_autotrader_audit.sh            # full collection
#   sudo bash collect_autotrader_audit.sh --no-okx   # skip OKX API reads
#   sudo bash collect_autotrader_audit.sh --no-candles
#
# What it does NOT do: place/cancel/modify orders, change TP/SL, stop/start/
# restart services, edit settings or flag files, write into /opt/autotrader
# or the user data directory. It only reads, and writes one bundle under the
# invoking user's home directory.
#
# OKX access uses GET endpoints only (balances, positions, fills, bills,
# order/algo history, public candles).
#
# Secrets: .env values whose names look secret are redacted, accounts.json is
# never copied, and every collected text file is scanned for the actual secret
# values from .env (plus common token formats) and scrubbed before packing.

set -u
umask 077

ROOT_LINK="${CBL_ROOT:-/opt/autotrader}"
ACCOUNT="${CBL_ACCOUNT:-chickenbananalab}"
EVIDENCE_DIR="${CBL_EVIDENCE:-/var/lib/autotrader/deployment-evidence}"
DATA_DAYS="${CBL_DATA_DAYS:-40}"
LOG_DAYS="${CBL_LOG_DAYS:-10}"
EVENT_DAYS="${CBL_EVENT_DAYS:-35}"
DO_OKX=1
DO_CANDLES=1
for arg in "$@"; do
  case "$arg" in
    --no-okx) DO_OKX=0 ;;
    --no-candles) DO_CANDLES=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ] && [ -z "${CBL_ALLOW_NONROOT:-}" ]; then
  echo "Run with sudo (needed to read journald and the service-owned data)." >&2
  exit 2
fi

STAMP="$(TZ=Asia/Seoul date +%Y%m%dT%H%M%S)KST"
INVOKER="${SUDO_USER:-$(id -un)}"
INVOKER_HOME="$(getent passwd "$INVOKER" | cut -d: -f6)"
OUT_BASE="${CBL_OUT_BASE:-${INVOKER_HOME:-/tmp}}"
OUT="$OUT_BASE/cbl_audit_$STAMP"
TOOLS="$(mktemp -d)"
trap 'rm -rf "$TOOLS"' EXIT
mkdir -p "$OUT"/{system,deploy,config,data,logs,okx}
STEPLOG="$OUT/collector_steps.log"

step() { echo "[$(TZ=Asia/Seoul date '+%F %T KST')] $*" | tee -a "$STEPLOG"; }
run() {  # run <outfile> <cmd...>  - never aborts the collection
  local f="$1"; shift
  { echo "\$ $*"; "$@" 2>&1; echo "[exit=$?]"; } >"$f" 2>&1
}

# ---------------------------------------------------------------- python tool
cat >"$TOOLS/tool.py" <<'PY'
import hashlib, json, os, re, shutil, sqlite3, stat, sys, time

SECRET_TOKENS = {"KEY", "APIKEY", "SECRET", "PASS", "PASSWORD", "PASSPHRASE",
                 "TOKEN", "WEBHOOK", "COOKIE", "SESSION", "PRIVATE",
                 "CREDENTIAL", "CREDENTIALS", "DSN"}
GENERIC_RES = [
    re.compile(rb"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}"),          # telegram bot token (also inside /bot<token>/ URLs)
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),                    # openai / anthropic
    re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}"),                   # google api key
    re.compile(rb"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(rb"(?i)(OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+"),
]
SECRET_FILE_RE = re.compile(r"(^\.env|accounts.*\.json$|\.pem$|\.key$|secret|token|cookie|credential|passw)", re.I)
SQLITE_EXT = (".sqlite", ".sqlite3", ".db")
SKIP_SIDECAR = ("-wal", "-shm", "-journal")


def is_secret_name(name):
    up = name.upper()
    parts = set(re.split(r"[^A-Z0-9]+", up))
    return bool(parts & SECRET_TOKENS) or up.endswith("CHAT_ID")


def parse_env(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


JSON_SECRET_KEYS = re.compile(r"pass|hash|salt|token|secret|api_?key|cookie", re.I)


def _json_secrets(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and JSON_SECRET_KEYS.search(str(k)) and len(v) >= 6:
                out.add(v)
            else:
                _json_secrets(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _json_secrets(v, out)


def secret_values(paths):
    vals = set()
    for p in paths:
        if p.endswith(".json"):
            try:
                with open(p, encoding="utf-8") as f:
                    _json_secrets(json.load(f), vals)
            except (OSError, ValueError):
                pass
            continue
        for k, v in parse_env(p).items():
            v2 = v.strip().strip('"').strip("'")
            if is_secret_name(k) and len(v2) >= 6:
                vals.add(v2)
    return sorted(vals, key=len, reverse=True)


def cmd_envredact(src, dst):
    env = parse_env(src)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("# redacted copy of %s (values of secret-looking keys hidden)\n" % src)
        for k, v in env.items():
            if is_secret_name(k):
                f.write("%s=<redacted set=%s len=%d>\n" % (k, "yes" if v else "no", len(v)))
            else:
                f.write("%s=%s\n" % (k, v))


def _sqlite_backup(src, dst):
    con = sqlite3.connect("file:%s?mode=ro" % src, uri=True, timeout=10)
    try:
        out = sqlite3.connect(dst)
        with out:
            con.backup(out)
        out.close()
    finally:
        con.close()


def _is_sqlite(path):
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def cmd_copydata(src, dst, days, max_mb, small_kb, manifest):
    now = time.time()
    since = now - float(days) * 86400
    max_b = float(max_mb) * 1024 * 1024
    small_b = float(small_kb) * 1024
    rows = []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "node_modules", "venv", ".venv")]
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, src)
            try:
                st = os.lstat(p)
            except OSError as e:
                rows.append((rel, -1, 0, "stat_error:%s" % e.__class__.__name__)); continue
            if stat.S_ISLNK(st.st_mode):
                rows.append((rel, st.st_size, st.st_mtime, "symlink->%s" % os.readlink(p))); continue
            if not stat.S_ISREG(st.st_mode):
                rows.append((rel, st.st_size, st.st_mtime, "not_regular")); continue
            if SECRET_FILE_RE.search(name):
                rows.append((rel, st.st_size, st.st_mtime, "skipped_secret_file")); continue
            if name.endswith(SKIP_SIDECAR) and os.path.exists(p[: -len(name.rsplit("-", 1)[1]) - 1]):
                rows.append((rel, st.st_size, st.st_mtime, "sqlite_sidecar(in_backup)")); continue
            take = st.st_size <= small_b or (st.st_mtime >= since and st.st_size <= max_b)
            if not take:
                rows.append((rel, st.st_size, st.st_mtime, "skipped_old_or_large")); continue
            target = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                if name.lower().endswith(SQLITE_EXT) or _is_sqlite(p):
                    _sqlite_backup(p, target)
                    action = "sqlite_backup"
                else:
                    shutil.copy2(p, target)
                    action = "copied"
            except Exception as e:  # noqa: BLE001 - keep collecting
                action = "copy_error:%s:%s" % (e.__class__.__name__, str(e)[:120])
            rows.append((rel, st.st_size, st.st_mtime, action))
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("path\tsize\tmtime_utc\taction\n")
        for rel, size, mt, action in rows:
            f.write("%s\t%d\t%s\t%s\n" % (rel, size, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mt)), action))


CODE_SKIP_DIRS = {"users", "logs", "venv", ".venv", "env", "__pycache__", ".git", "node_modules",
                  ".pytest_cache", ".mypy_cache", "backups", "backup"}
CODE_SKIP_RE = re.compile(r"(^\.env|accounts.*\.json$|\.(sqlite3?|db|db-wal|db-shm|jsonl|log|gz|tgz|tar|zip|pem|key|pyc)$)", re.I)


def cmd_codecopy(src, dst, listing, max_kb):
    max_b = float(max_kb) * 1024
    rows = []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        rel_dir = os.path.relpath(dirpath, src)
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if d in CODE_SKIP_DIRS or os.path.islink(full):
                tgt = os.readlink(full) if os.path.islink(full) else ""
                rows.append((os.path.normpath(os.path.join(rel_dir, d)) + "/", 0, "skipped_dir" + ("->" + tgt if tgt else "")))
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.normpath(os.path.join(rel_dir, name))
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                rows.append((rel, 0, "symlink->%s" % os.readlink(p))); continue
            if CODE_SKIP_RE.search(name) or SECRET_FILE_RE.search(name):
                rows.append((rel, st.st_size, "skipped_data_or_secret")); continue
            if st.st_size > max_b:
                rows.append((rel, st.st_size, "skipped_large")); continue
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            target = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(p, target)
            rows.append((rel, st.st_size, "sha256=" + h.hexdigest()))
    with open(listing, "w", encoding="utf-8") as f:
        f.write("path\tsize\tinfo\n")
        for r in rows:
            f.write("%s\t%d\t%s\n" % r)


def cmd_scrub(out_dir, report, *env_paths):
    vals = [v.encode() for v in secret_values(env_paths)]
    # long values are matched anywhere (e.g. inside /bot<token>/ URLs); short ones
    # (chat ids) only as whole tokens so order ids containing the digits survive.
    pats = [re.compile(re.escape(v)) if len(v) >= 16 else
            re.compile(rb"(?<![A-Za-z0-9])" + re.escape(v) + rb"(?![A-Za-z0-9])") for v in vals]
    hits = []
    for dirpath, _d, filenames in os.walk(out_dir):
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p) or p == report:
                continue
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            binary = name.lower().endswith(SQLITE_EXT) or data[:16] == b"SQLite format 3\x00"
            found = [i for i, pat in enumerate(pats) if pat.search(data)]
            if binary:
                if found:
                    os.remove(p)
                    hits.append((p, "REMOVED_binary_with_secret"))
                continue
            new = data
            for pat in pats:
                new = pat.sub(b"<REDACTED_SECRET>", new)
            for rx in GENERIC_RES:
                if rx.groups:
                    new = rx.sub(lambda m: m.group(1) + b"<REDACTED_TOKEN>", new)
                else:
                    new = rx.sub(b"<REDACTED_TOKEN>", new)
            if new != data:
                with open(p, "wb") as f:
                    f.write(new)
                hits.append((p, "scrubbed"))
    with open(report, "w", encoding="utf-8") as f:
        f.write("secret values checked: %d\n" % len(vals))
        if not vals:
            f.write("WARNING: no secret values were loaded from .env; only generic token patterns were scrubbed\n")
        for p, a in hits:
            f.write("%s\t%s\n" % (os.path.relpath(p, out_dir), a))


if __name__ == "__main__":
    cmd = sys.argv[1]
    globals()["cmd_" + cmd](*sys.argv[2:])
PY

cat >"$TOOLS/okx_pull.py" <<'PY'
"""Read-only OKX evidence pull. GET endpoints only; no order/position mutation."""
import json, os, sys, time, traceback

env_path, out_dir, since_days, do_candles = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4] == "1"


def parse_env(path):
    env = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


import ccxt  # noqa: E402

env = parse_env(env_path)
ex = ccxt.okx({
    "apiKey": env.get("OKX_API_KEY", ""),
    "secret": env.get("OKX_API_SECRET", ""),
    "password": env.get("OKX_API_PASSPHRASE", ""),
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})
del env

now_ms = int(time.time() * 1000)
since_ms = now_ms - int(since_days * 86400 * 1000)
errors = []
meta = {"generated_at_ms": now_ms, "since_ms": since_ms, "ccxt": ccxt.__version__, "calls": {}}


def save(name, obj):
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


RETRYABLE = ("RateLimit", "DDoS", "NetworkError", "RequestTimeout", "ExchangeNotAvailable", "OnMaintenance")


def call(label, method, params, attempts=4):
    for attempt in range(attempts):
        try:
            r = getattr(ex, method)(params)
            meta["calls"][label] = meta["calls"].get(label, 0) + 1
            code = str(r.get("code", "0"))
            if code in ("50011", "50061") and attempt + 1 < attempts:   # OKX rate limit
                time.sleep(2 * (attempt + 1)); continue
            if code != "0":
                errors.append({"label": label, "params": params, "code": code, "msg": r.get("msg")})
            return r.get("data") or []
        except Exception as e:  # noqa: BLE001
            names = [c.__name__ for c in type(e).__mro__]
            if attempt + 1 < attempts and any(n.startswith(RETRYABLE) for n in names):
                time.sleep(2 * (attempt + 1)); continue
            errors.append({"label": label, "params": params, "error": "%s: %s" % (e.__class__.__name__, str(e)[:300])})
            return None


def paginate(label, method, params, id_field, ts_field, max_pages=400, pause=0.25):
    out, after, seen = [], None, set()
    for _ in range(max_pages):
        p = dict(params, limit="100")
        if after:
            p["after"] = after
        data = call(label, method, p)
        if not data:
            break
        new = 0
        for d in data:
            key = (d.get(id_field), d.get(ts_field), d.get("instId"))
            if key not in seen:
                seen.add(key); out.append(d); new += 1
        oldest = min(int(d.get(ts_field) or 0) for d in data)
        after = data[-1].get(id_field)
        if new == 0 or oldest < since_ms or len(data) < 100 or not after:
            break
        time.sleep(pause)
    return out


try:
    save("account_config", call("config", "privateGetAccountConfig", {}))
    save("account_balance", call("balance", "privateGetAccountBalance", {}))
    save("positions_open", call("positions", "privateGetAccountPositions", {"instType": "SWAP"}))
    save("orders_pending", call("orders_pending", "privateGetTradeOrdersPending", {"instType": "SWAP"}))
    algo_pending = {}
    for t in ("conditional", "oco", "trigger", "move_order_stop"):
        algo_pending[t] = call("algo_pending_" + t, "privateGetTradeOrdersAlgoPending", {"ordType": t})
        time.sleep(0.2)
    save("algo_pending", algo_pending)

    save("positions_history", paginate("positions_history", "privateGetAccountPositionsHistory",
                                       {"instType": "SWAP"}, "uTime", "uTime"))
    save("fills_history", paginate("fills_history", "privateGetTradeFillsHistory",
                                   {"instType": "SWAP"}, "billId", "ts"))
    save("orders_history_archive", paginate("orders_history_archive", "privateGetTradeOrdersHistoryArchive",
                                            {"instType": "SWAP"}, "ordId", "cTime"))
    algo_hist = {}
    for t in ("conditional", "oco", "trigger", "move_order_stop"):
        for s in ("effective", "canceled", "order_failed"):
            algo_hist["%s_%s" % (t, s)] = paginate("algo_history", "privateGetTradeOrdersAlgoHistory",
                                                   {"ordType": t, "state": s}, "algoId", "cTime")
    save("algo_history", algo_hist)
    save("bills_7d", paginate("bills_7d", "privateGetAccountBills", {}, "billId", "ts", pause=0.45))
    save("bills_archive", paginate("bills_archive", "privateGetAccountBillsArchive", {}, "billId", "ts", pause=0.45))
    save("asset_bills", paginate("asset_bills", "privateGetAssetBills", {}, "billId", "ts", pause=0.6))

    if do_candles:
        inst = set()
        for name in ("positions_open", "positions_history", "fills_history"):
            try:
                with open(os.path.join(out_dir, name + ".json"), encoding="utf-8") as f:
                    for d in json.load(f) or []:
                        if d.get("instId"):
                            inst.add(d["instId"])
            except Exception:  # noqa: BLE001
                pass
        plan = [("1m", 3), ("5m", since_days), ("1H", since_days + 10)]
        candles = {}
        deadline = time.time() + 20 * 60
        for inst_id in sorted(inst):
            for bar, days in plan:
                start = now_ms - int(days * 86400 * 1000)
                rows, after = [], None
                while time.time() < deadline:
                    p = {"instId": inst_id, "bar": bar, "limit": "100"}
                    if after:
                        p["after"] = after
                    data = call("candles", "publicGetMarketHistoryCandles", p)
                    if not data:
                        break
                    rows.extend(data)
                    after = data[-1][0]
                    if int(after) < start or len(data) < 100:
                        break
                    time.sleep(0.11)
                candles["%s|%s" % (inst_id, bar)] = rows
        meta["candles_truncated_by_deadline"] = time.time() >= deadline
        save("candles", candles)
except Exception:  # noqa: BLE001
    errors.append({"fatal": traceback.format_exc()[-2000:]})
finally:
    meta["errors"] = errors
    save("_meta", meta)
print("okx pull done; errors=%d" % len(errors))
PY

PYSYS="$(command -v python3 || true)"
if [ -z "$PYSYS" ]; then echo "python3 not found" >&2; exit 2; fi
tool() { "$PYSYS" -B -I "$TOOLS/tool.py" "$@"; }

# ----------------------------------------------------------- 1. live release
step "1/8 active release and system state"
ACTIVE="$(readlink -f "$ROOT_LINK" 2>/dev/null || true)"
RELEASES_ROOT="$(dirname "${ACTIVE:-/nonexistent}")"
USER_DIR="$ROOT_LINK/users/$ACCOUNT"
USER_DIR_REAL="$(readlink -f "$USER_DIR" 2>/dev/null || echo "$USER_DIR")"
{
  echo "collected_at_kst=$(TZ=Asia/Seoul date '+%F %T %Z')"
  echo "collected_at_utc=$(date -u '+%F %T %Z')"
  echo "host=$(hostname)"
  echo "root_link=$ROOT_LINK"
  echo "root_link_raw=$(readlink "$ROOT_LINK" 2>/dev/null)"
  echo "active_release=$ACTIVE"
  echo "active_release_name=$(basename "${ACTIVE:-none}")"
  echo "active_release_ctime=$(stat -c '%z' "$ROOT_LINK" 2>/dev/null)"
  echo "releases_root=$RELEASES_ROOT"
  echo "user_dir=$USER_DIR -> $USER_DIR_REAL"
  echo "server_tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)"
} >"$OUT/system/active_release.txt"
cat "$OUT/system/active_release.txt" | tee -a "$STEPLOG"

run "$OUT/system/uptime.txt" uptime
run "$OUT/system/df.txt" df -h
run "$OUT/system/free.txt" free -m
run "$OUT/system/os.txt" cat /etc/os-release
run "$OUT/system/units_all.txt" systemctl list-units --all --no-pager
run "$OUT/system/unit_files.txt" systemctl list-unit-files --no-pager
run "$OUT/system/timers.txt" systemctl list-timers --all --no-pager
run "$OUT/system/failed.txt" systemctl --failed --no-pager
UNITS="$(systemctl list-units --all --no-legend --plain 2>/dev/null | awk '{print $1}' | grep -iE 'autotrader|trader|candidate|core' || true)"
UNITS="$UNITS $(systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -iE 'autotrader|trader|candidate' || true)"
UNITS="$(echo $UNITS | tr ' ' '\n' | sort -u | tr '\n' ' ')"
echo "$UNITS" >"$OUT/system/units_matched.txt"
for u in $UNITS; do
  run "$OUT/system/unit_${u}_status.txt" systemctl status "$u" --no-pager -l
  run "$OUT/system/unit_${u}_cat.txt" systemctl cat "$u" --no-pager
  run "$OUT/system/unit_${u}_show.txt" systemctl show "$u" --no-pager -p Id,ActiveState,SubState,MainPID,ExecMainStartTimestamp,NRestarts,Result,User,WorkingDirectory,ExecStart,Restart,UnitFileState
done
run "$OUT/system/ps.txt" ps -eo pid,ppid,user,lstart,etime,pcpu,pmem,rss,args --sort=start_time
for pid in $(pgrep -f -i 'autotrader|trader|waitress|gunicorn|web_app' 2>/dev/null); do
  { echo "pid=$pid"; tr '\0' ' ' </proc/$pid/cmdline 2>/dev/null; echo; echo "cwd=$(readlink /proc/$pid/cwd 2>/dev/null)";
    echo "exe=$(readlink /proc/$pid/exe 2>/dev/null)"; echo "threads=$(ls /proc/$pid/task 2>/dev/null | wc -l)";
    echo "open_files:"; ls -l /proc/$pid/fd 2>/dev/null | awk '{print $NF}' | grep -vE '^(socket|pipe|anon_inode|/dev)' | sort -u | head -200; } \
    >"$OUT/system/proc_$pid.txt" 2>&1
done
run "$OUT/system/listen.txt" ss -ltnp
run "$OUT/system/crontab_root.txt" crontab -l -u root
SVC_USER="$(stat -c %U "$USER_DIR_REAL" 2>/dev/null || echo "")"
[ -n "$SVC_USER" ] && run "$OUT/system/crontab_$SVC_USER.txt" crontab -l -u "$SVC_USER"
run "$OUT/system/cron_d.txt" ls -la /etc/cron.d

# ------------------------------------------------------------ 2. deployments
step "2/8 deployment history"
run "$OUT/deploy/releases_ls.txt" ls -la --time-style=full-iso "$RELEASES_ROOT"
run "$OUT/deploy/opt_ls.txt" ls -la --time-style=full-iso /opt
run "$OUT/deploy/evidence_ls.txt" ls -la --time-style=full-iso "$EVIDENCE_DIR"
# small manifests/notes of the 25 newest releases
for d in $(ls -1dt "$RELEASES_ROOT"/*/ 2>/dev/null | head -25); do
  n="$(basename "$d")"; mkdir -p "$OUT/deploy/releases/$n"
  find "$d" -maxdepth 1 -type f \( -iname '*.json' -o -iname '*.md' -o -iname '*.txt' \) -size -1024k \
    ! -iname '.env*' ! -iname 'accounts*.json' -exec cp -p {} "$OUT/deploy/releases/$n/" \; 2>/dev/null
  stat -c '%n	%z' "$d" >"$OUT/deploy/releases/$n/_dir_ctime.txt" 2>/dev/null
done
for d in $(ls -1dt "$EVIDENCE_DIR"/*/ 2>/dev/null | head -12); do
  n="$(basename "$d")"; mkdir -p "$OUT/deploy/evidence/$n"
  find "$d" -maxdepth 2 -type f -size -2048k \( -iname '*.json' -o -iname '*.md' -o -iname '*.txt' -o -iname '*.log' \) \
    ! -iname '.env*' -exec cp -p {} "$OUT/deploy/evidence/$n/" \; 2>/dev/null
done

# ------------------------------------------------------------- 3. live code
step "3/8 active release source (code only, no data/secrets)"
if [ -n "$ACTIVE" ] && [ -d "$ACTIVE" ]; then
  tool codecopy "$ACTIVE" "$OUT/code" "$OUT/release_tree.tsv" 5120 >>"$STEPLOG" 2>&1
fi

# ------------------------------------------------------------- 4. settings
step "4/8 settings and flag files (secrets redacted)"
if [ -f "$USER_DIR_REAL/.env" ]; then
  tool envredact "$USER_DIR_REAL/.env" "$OUT/config/user_env_redacted.txt"
  stat -c '%n	%y	%U' "$USER_DIR_REAL/.env" >"$OUT/config/user_env_stat.txt"
fi
for f in "$ACTIVE"/.env "$ACTIVE"/*.env; do
  [ -f "$f" ] && tool envredact "$f" "$OUT/config/$(basename "$f")_redacted.txt"
done
find "$USER_DIR_REAL" -maxdepth 2 \( -name '*.enabled' -o -name '*.disabled' -o -name '*flag*' -o -name '*.intent*' \) \
  -printf '%p\t%s\t%TY-%Tm-%Td %TH:%TM:%TS\t%u\n' >"$OUT/config/flag_files.tsv" 2>&1
find "$ACTIVE" -maxdepth 1 \( -name '*.enabled' -o -name '*.disabled' \) \
  -printf '%p\t%s\t%TY-%Tm-%Td %TH:%TM:%TS\t%u\n' >>"$OUT/config/flag_files.tsv" 2>/dev/null
if [ -f "$ACTIVE/accounts.json" ]; then
  "$PYSYS" -B -I -c 'import json,sys
d=json.load(open(sys.argv[1]))
def shape(x):
    if isinstance(x,dict): return {k:(shape(v) if k.lower() not in ("password","password_hash","hash","salt","token","secret") else "<redacted>") for k,v in x.items()}
    if isinstance(x,list): return [shape(v) for v in x]
    return x
print(json.dumps(shape(d),ensure_ascii=False,indent=1))' "$ACTIVE/accounts.json" >"$OUT/config/accounts_redacted.json" 2>&1
fi

# ------------------------------------------------------------- 5. user data
step "5/8 user data: ledgers, journals, state (last ${DATA_DAYS}d + all small files)"
tool copydata "$USER_DIR_REAL" "$OUT/data/user" "$DATA_DAYS" 200 1024 "$OUT/data/user_manifest.tsv" >>"$STEPLOG" 2>&1
# runtime data kept in the release root (outside users/)
mkdir -p "$OUT/data/release_root"
find "$ACTIVE" -maxdepth 2 -type f \( -name '*.jsonl' -o -name '*.sqlite*' -o -name '*.db' -o -name '*state*.json' \) \
  ! -path '*/users/*' -printf '%p\t%s\t%TY-%Tm-%Td %TH:%TM\n' >"$OUT/data/release_root_datafiles.tsv" 2>/dev/null

# ------------------------------------------------------------------ 6. logs
step "6/8 logs: journald + app logs (last ${LOG_DAYS}d), key events (last ${EVENT_DAYS}d)"
SINCE_LOG="$(date -d "-$LOG_DAYS days" '+%F %T')"
for u in $UNITS; do
  journalctl -u "$u" --since "$SINCE_LOG" --no-pager -o short-iso >"$OUT/logs/journal_${u}.log" 2>&1
done
journalctl -k --since "$SINCE_LOG" --no-pager -o short-iso | grep -iE 'oom|killed process' >"$OUT/logs/kernel_oom.log" 2>/dev/null
LOGDIRS=""
for d in "$ROOT_LINK/logs" "$USER_DIR_REAL/logs" "$RELEASES_ROOT"/*/logs; do
  [ -d "$d" ] && LOGDIRS="$LOGDIRS $(readlink -f "$d")"
done
LOGDIRS="$(echo $LOGDIRS | tr ' ' '\n' | sort -u | tr '\n' ' ')"
echo "$LOGDIRS" >"$OUT/logs/log_dirs.txt"
[ -z "${LOGDIRS// /}" ] && LOGDIRS="/nonexistent-cbl-logdir"
find $LOGDIRS -maxdepth 1 -type f -printf '%p\t%s\t%TY-%Tm-%Td %TH:%TM\n' 2>/dev/null | sort -k3 >"$OUT/logs/log_files.tsv"
mkdir -p "$OUT/logs/app"
find $LOGDIRS -maxdepth 1 -type f -mtime -"$LOG_DAYS" -size -300M 2>/dev/null | while read -r f; do
  key="$(echo "$f" | sed 's#^/##; s#/#__#g')"; cp -p "$f" "$OUT/logs/app/$key"
done
EVENT_RE='ENTRY_SKIPPED|entry_not_permitted|signal_expired|HALT|KILL|kill_switch|ORDER|FILL|CLOSE|REDUCE|EXTERNAL|external_close|LIQUID|PROTECTION|SLTP|STOP_LOSS|TAKE_PROFIT|OCO|ROUTE|CORE_[A-D]|Candidate|Traceback|ERROR|CRITICAL|rate.?limit|429|timeout|50011|51000'
{
  find $LOGDIRS -maxdepth 1 -type f -mtime -"$EVENT_DAYS" 2>/dev/null | sort | while read -r f; do
    grep -HaE "$EVENT_RE" "$f" 2>/dev/null
  done
  for u in $UNITS; do
    journalctl -u "$u" --since "$(date -d "-$EVENT_DAYS days" '+%F %T')" --no-pager -o short-iso 2>/dev/null | grep -aE "$EVENT_RE" | sed "s#^#journal:$u:#"
  done
} >"$OUT/logs/key_events_${EVENT_DAYS}d.log"

# -------------------------------------------------------------- 7. OKX reads
if [ "$DO_OKX" = "1" ] && [ -f "$USER_DIR_REAL/.env" ]; then
  step "7/8 OKX read-only ledger pull (GET only)"
  PYBOT=""
  for u in $UNITS; do
    cand="$(systemctl show "$u" -p ExecStart --value 2>/dev/null | grep -oE 'path=[^ ;]+' | head -1 | cut -d= -f2)"
    [ -n "$cand" ] && PYBOT="$PYBOT $cand"
  done
  for c in $PYBOT "$ACTIVE/venv/bin/python" "$ACTIVE/.venv/bin/python" "$RELEASES_ROOT/venv/bin/python" \
           /opt/autotrader-venv/bin/python /opt/venv/bin/python python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -B -I -c 'import ccxt' >/dev/null 2>&1; then PYOKX="$c"; break; fi
  done
  if [ -n "${PYOKX:-}" ]; then
    echo "python=$PYOKX" >>"$STEPLOG"
    "$PYOKX" -B -I "$TOOLS/okx_pull.py" "$USER_DIR_REAL/.env" "$OUT/okx" "$DATA_DAYS" "$DO_CANDLES" >>"$STEPLOG" 2>&1
  else
    step "  no python with ccxt found; OKX pull skipped"
  fi
else
  step "7/8 OKX pull skipped"
fi

# ------------------------------------------------------------ 8. scrub+pack
step "8/8 secret scrub and packing"
tool scrub "$OUT" "$OUT/secret_scrub_report.txt" "$USER_DIR_REAL/.env" "$ACTIVE/.env" "$ACTIVE/accounts.json" >>"$STEPLOG" 2>&1
( cd "$OUT" && find . -type f -printf '%P\t%s\n' | sort >"$OUT/_bundle_files.tsv" )
TAR="$OUT_BASE/cbl_audit_$STAMP.tar.gz"
tar -C "$OUT_BASE" -czf "$TAR" "cbl_audit_$STAMP"
rm -rf "$OUT"
if [ -n "${SUDO_USER:-}" ]; then chown "$SUDO_USER" "$TAR"; fi
chmod 600 "$TAR"
echo
echo "DONE"
echo "bundle: $TAR"
echo "size:   $(du -h "$TAR" | cut -f1)"
echo "sha256: $(sha256sum "$TAR" | cut -d' ' -f1)"
