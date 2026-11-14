#!/usr/bin/env bash
# The Earth — 구글 클라우드 VM(Debian/Ubuntu)에 설치하기
#
# 사용 (VM 안에서):
#   curl -fsSL https://raw.githubusercontent.com/mingmingki/chickenbananalab/claude/earth-game-dev-mefzml/the-earth/deploy/gcp-setup.sh | sudo bash -s -- 관리자아이디
#
# 하는 일
#   1. 노드 22, git, cloudflared 설치 (메모리가 적으면 스왑 1GB)
#   2. 게임을 /opt/the-earth-src 에 받고, 세계·계정 DB는 /var/lib/the-earth 에 둔다
#   3. 게임 서버를 systemd 서비스(the-earth)로 등록 — 재부팅해도 자동으로 켜진다
#      같은 VM의 다른 프로그램(예: 자동매매)이 우선하도록 CPU·메모리 상한과 낮은 우선순위를 건다
#   4. Cloudflare 무료 터널(the-earth-tunnel)로 https 주소를 만든다 — 방화벽 포트를 열 필요가 없다
#   5. earth-url(주소 보기), earth-update(새 버전 받기) 명령을 만든다
#
# 다시 실행해도 안전하다 (이미 있으면 갱신만 한다).
#
# 다른 프로그램(예: 자동매매)이 도는 VM 에 같이 올릴 때
#   · 남은 메모리가 700MB 보다 적으면 설치하지 않는다 (EARTH_FORCE=1 로 무시 가능)
#   · 그 프로그램의 서비스·파일·포트는 건드리지 않는다. 게임은 낮은 우선순위로, CPU·메모리 상한 안에서만 돈다
#   · 이미 깔린 cloudflared 와 그 설정(/etc/cloudflared)은 그대로 둔다 — 게임 터널에게만 안 보이게 가린다
set -euo pipefail

ADMIN="${1:-${EARTH_ADMIN:-}}"
BRANCH="${EARTH_BRANCH:-claude/earth-game-dev-mefzml}"
REPO="${EARTH_REPO:-https://github.com/mingmingki/chickenbananalab}"
PORT="${EARTH_PORT:-3000}"
SRC=/opt/the-earth-src
APP="$SRC/the-earth"
DATA=/var/lib/the-earth
CPU_QUOTA="${EARTH_CPU_QUOTA:-60%}"
MEM_MAX="${EARTH_MEM_MAX:-600M}"

say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "sudo 로 실행해 주세요."
[ -n "$ADMIN" ] || die "관리자 아이디를 넣어 주세요. 예: ... | sudo bash -s -- minki"
case "$ADMIN" in *[!A-Za-z0-9_.,-]*) die "관리자 아이디는 영문·숫자·_ . - 만 쓸 수 있어요 (여러 명이면 쉼표로)." ;; esac
command -v apt-get >/dev/null || die "Debian/Ubuntu VM 에서만 됩니다."
command -v systemctl >/dev/null || die "systemd 가 없는 환경입니다."

# 남은 메모리 확인 — 같은 VM 의 다른 프로그램이 모자라지 않게
avail_mb=$(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) / 1024 ))
echo "남은 메모리: ${avail_mb}MB"
if ! systemctl is-active --quiet the-earth && [ "$avail_mb" -lt 700 ] && [ "${EARTH_FORCE:-0}" != 1 ]; then
  die "남은 메모리가 ${avail_mb}MB 라서 같이 올리기엔 빠듯해요 (700MB 이상 필요). 이 VM 에는 설치하지 않았어요."
fi

# 포트가 다른 프로그램에 쓰이고 있으면 멈춘다 (이미 깔린 the-earth 는 괜찮다)
if ss -ltnH "( sport = :$PORT )" 2>/dev/null | grep -q . && ! systemctl is-active --quiet the-earth; then
  die "포트 $PORT 를 다른 프로그램이 쓰고 있어요. 다른 번호로: ... | sudo EARTH_PORT=3100 bash -s -- $ADMIN"
fi

say "필요한 프로그램 설치"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates >/dev/null

# 메모리가 1.5GB 보다 적고 스왑이 없으면 1GB 스왑 (e2-micro 에서 npm 설치가 멈추지 않게)
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 1500000 ] && ! swapon --noheadings --show | grep -q .; then
  say "메모리가 적어 스왑 1GB 를 만듭니다"
  if [ ! -f /swapfile-earth ]; then
    fallocate -l 1G /swapfile-earth || dd if=/dev/zero of=/swapfile-earth bs=1M count=1024 status=none
    chmod 600 /swapfile-earth
    mkswap /swapfile-earth >/dev/null
  fi
  swapon /swapfile-earth 2>/dev/null || true
  grep -q '^/swapfile-earth ' /etc/fstab || echo '/swapfile-earth none swap sw 0 0' >> /etc/fstab
fi

# 노드 22.13 이상 (내장 SQLite)
node_ok() { command -v node >/dev/null && node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>22||(a===22&&b>=13)?0:1)'; }
if ! node_ok; then
  say "노드 22 설치"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
node_ok || die "노드 22.13 이상을 설치하지 못했어요."
NODE_BIN="$(command -v node)"
echo "노드 $(node -v)"

# cloudflared (https 터널)
if ! command -v cloudflared >/dev/null; then
  say "cloudflared 설치"
  arch="$(dpkg --print-architecture)"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/cloudflared.deb" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  dpkg -i "$tmp/cloudflared.deb" >/dev/null
  rm -rf "$tmp"
fi
CF_BIN="$(command -v cloudflared)"

say "게임 받기 ($BRANCH)"
id earth >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin earth
mkdir -p "$DATA"
chown earth:earth "$DATA"
chmod 750 "$DATA"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch --depth 1 origin "$BRANCH"
  git -C "$SRC" reset --hard FETCH_HEAD
else
  rm -rf "$SRC"
  git clone --depth 1 --filter=blob:none --sparse -b "$BRANCH" "$REPO" "$SRC"
  git -C "$SRC" sparse-checkout set the-earth
fi
cd "$APP"
npm ci --omit=dev --no-audit --no-fund --loglevel=error

say "서비스 등록"
cat > /etc/systemd/system/the-earth.service <<EOF
[Unit]
Description=The Earth game server
After=network-online.target
Wants=network-online.target

[Service]
User=earth
Group=earth
WorkingDirectory=$APP
Environment=NODE_ENV=production
Environment=DATA_DIR=$DATA
Environment=PORT=$PORT
Environment=ADMIN_USERS=$ADMIN
Environment=TRUST_PROXY=1
ExecStart=$NODE_BIN --disable-warning=ExperimentalWarning server/index.js
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
# 같은 VM 의 다른 프로그램이 먼저 — 게임은 낮은 우선순위, CPU·메모리 상한
Nice=10
CPUQuota=$CPU_QUOTA
MemoryMax=$MEM_MAX
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$DATA

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/the-earth-tunnel.service <<EOF
[Unit]
Description=The Earth https tunnel (Cloudflare quick tunnel)
After=the-earth.service network-online.target
Wants=network-online.target

[Service]
DynamicUser=yes
Environment=HOME=/tmp
# 이 VM 에 이미 Cloudflare 이름 있는 터널 설정이 있으면 무료 터널이 켜지지 않는다.
# 그 설정은 건드리지 않고, 게임 터널에게만 안 보이게 가린다.
InaccessiblePaths=-/etc/cloudflared -/usr/local/etc/cloudflared
ExecStart=$CF_BIN tunnel --no-autoupdate --url http://127.0.0.1:$PORT
Restart=always
RestartSec=10
Nice=10

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/earth-url <<'EOF'
#!/usr/bin/env bash
# 지금 게임 주소 (터널이 다시 켜지면 주소가 바뀐다)
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"
url=$(journalctl -b -u the-earth-tunnel -o cat --no-pager 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)
if [ -n "$url" ]; then echo "$url"; else echo "아직 주소가 없어요. 잠시 뒤 다시: earth-url"; exit 1; fi
EOF
chmod 755 /usr/local/bin/earth-url

cat > /usr/local/bin/earth-update <<EOF
#!/usr/bin/env bash
# 새 버전 받기 — 세계·계정은 그대로 남는다 (주소도 그대로)
set -euo pipefail
[ "\$(id -u)" = 0 ] || { echo "sudo earth-update 로 실행해 주세요."; exit 1; }
git -C "$SRC" fetch --depth 1 origin "$BRANCH"
git -C "$SRC" reset --hard FETCH_HEAD
cd "$APP" && npm ci --omit=dev --no-audit --no-fund --loglevel=error
systemctl restart the-earth
sleep 3
systemctl is-active --quiet the-earth && echo "업데이트 완료: \$(git -C "$SRC" log -1 --format='%h %s')"
EOF
chmod 755 /usr/local/bin/earth-update

systemctl daemon-reload
systemctl enable --now the-earth.service >/dev/null 2>&1
systemctl restart the-earth.service
# 터널이 이미 돌고 있으면 그대로 둔다 (주소가 바뀌지 않게)
systemctl is-active --quiet the-earth-tunnel || systemctl restart the-earth-tunnel.service
systemctl enable the-earth-tunnel.service >/dev/null 2>&1

say "게임 서버 켜지는 중"
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 || die "게임 서버가 켜지지 않았어요. 기록 보기: sudo journalctl -u the-earth -n 50"

say "https 주소 만드는 중"
url=""
for _ in $(seq 1 30); do
  url=$(journalctl -b -u the-earth-tunnel -o cat --no-pager 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)
  [ -n "$url" ] && break
  sleep 2
done

echo
echo "────────────────────────────────────────────"
if [ -n "$url" ]; then
  echo " 🌍 게임 주소: $url"
else
  echo " 주소가 아직 안 나왔어요. 잠시 뒤: earth-url"
fi
echo " 관리자 아이디: $ADMIN (이 아이디로 가입하면 관리자)"
echo " 주소 다시 보기: earth-url"
echo " 새 버전 받기:   sudo earth-update"
echo " 기록 보기:      sudo journalctl -u the-earth -f"
echo " ※ VM 을 다시 켜면 주소가 바뀝니다 (세계·계정은 그대로)."
echo "────────────────────────────────────────────"
