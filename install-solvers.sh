#!/bin/bash
# ============================================================
# Solver Auto-Install & Auto-Start for hermes container
# Target: 172.17.0.2 (ports 8877 Turnstile, 8866 reCAPTCHA)
# Usage on EC2 host:
#   docker cp install-solvers.sh hermes:/tmp/install-solvers.sh
#   docker exec hermes bash -c "API_KEY=YOUR_KEY bash /tmp/install-solvers.sh"
# ============================================================

set -e
HERMES_IP="172.17.0.2"
TPORT=8877   # Turnstile
RPORT=8866   # reCAPTCHA

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── API Key ──────────────────────────────────────────────────
: "${API_KEY:?Please set API_KEY: docker exec hermes bash -c 'API_KEY=...'}"

info "Starting install on hermes container (target: $HERMES_IP)"

# ── Step 1: System deps ──────────────────────────────────────
info "Step 1/6 — System dependencies..."
docker exec hermes bash -c "
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq 2>/dev/null
  dpkg -l git >/dev/null 2>&1 || apt-get install -y -qq git 2>/dev/null
  dpkg -l python3 >/dev/null 2>&1 || apt-get install -y -qq python3 python3-pip 2>/dev/null
  dpkg -l xvfb >/dev/null 2>&1 || apt-get install -y -qq xvfb 2>/dev/null
  echo OK
"
echo -e "${GREEN}✅${NC} System deps ready"

# ── Step 2: Clone / update repos ────────────────────────────
info "Step 2/6 — Cloning solver repos..."
docker exec hermes bash -c "
  export GIT_TERMINAL_PROMPT=0
  cd /opt
  [ -d turnstile-solver ] && echo 'Turnstile exists' || git clone -q https://github.com/icemellow-me/turnstile-solver.git
  [ -d recaptcha-v2-solver ] && echo 'reCAPTCHA exists' || git clone -q https://github.com/icemellow-me/recaptcha-v2-solver.git
"
echo -e "${GREEN}✅${NC} Repos ready"

# ── Step 3: Python deps ──────────────────────────────────────
info "Step 3/6 — Python dependencies..."
docker exec hermes bash -c "
  pip install -q --break-system-packages fastapi uvicorn websockets pydantic httpx 2>/dev/null
  pip install -q --break-system-packages playwright 2>/dev/null
  python3 -m playwright install chromium 2>&1 | tail -3
"
echo -e "${GREEN}✅${NC} Python deps ready"

# ── Step 4: Kill old instances ───────────────────────────────
info "Step 4/6 — Stopping old solvers..."
docker exec hermes bash -c "
  pkill -f 'solver-server.py' 2>/dev/null || true
  pkill -f Xvfb 2>/dev/null || true
  sleep 2
"
echo -e "${GREEN}✅${NC} Old instances stopped"

# ── Step 5: Start solvers ────────────────────────────────────
info "Step 5/6 — Starting solvers on $HERMES_IP (ports $TPORT and $RPORT)..."

# Start Xvfb for headless Chrome
docker exec -d hermes bash -c "
  export DISPLAY=:99
  pgrep Xvfb >/dev/null || Xvfb :99 -screen 0 1280x720x24 &
  echo 'Xvfb started'
"

sleep 2

# Start Turnstile solver
docker exec -d hermes bash -c "
  export DISPLAY=:99
  export XDG_RUNTIME_DIR=/tmp/runtime-\$$
  mkdir -p \$XDG_RUNTIME_DIR
  cd /opt/turnstile-solver
  API_KEY=$API_KEY nohup python3 solver-server.py --api-key $API_KEY --port $TPORT >/tmp/turnstile.log 2>&1 &
  echo \"Turnstile PID: \$!\"
"

# Start reCAPTCHA solver
docker exec -d hermes bash -c "
  export DISPLAY=:99
  export XDG_RUNTIME_DIR=/tmp/runtime-\$$
  mkdir -p \$XDG_RUNTIME_DIR
  cd /opt/recaptcha-v2-solver
  API_KEY=$API_KEY nohup python3 solver-server.py --api-key $API_KEY --port $RPORT >/tmp/recaptcha.log 2>&1 &
  echo \"reCAPTCHA PID: \$!\"
"

# ── Step 6: Verify ──────────────────────────────────────────
info "Step 6/6 — Verifying..."
sleep 8

for name in "Turnstile:$TPORT" "reCAPTCHA:$RPORT"; do
  svc="${name%:*}"
  port="${name#*:}"
  
  # Test inside container
  if docker exec hermes bash -c "curl -sf http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ $svc${NC} ($port) — HEALTHY"
  else
    echo -e "${RED}❌ $svc${NC} ($port) — not responding"
    echo "   Log: docker exec hermes tail -15 /tmp/${svc,,}.log"
  fi
done

echo ""
echo -e "${GREEN}=== Done! ===${NC}"
echo "Logs:  docker exec hermes tail -20 /tmp/turnstile.log"
echo "       docker exec hermes tail -20 /tmp/recaptcha.log"
echo ""
echo "API URLs (from inside hermes):"
echo "  Turnstile:  http://127.0.0.1:$TPORT"
echo "  reCAPTCHA:  http://127.0.0.1:$RPORT"
echo ""
echo "Test 2captcha API:"
echo "  curl -X POST http://\$HOST:$TPORT/in.php -d 'key=\$API_KEY&method=userrecaptcha&googlekey=YOUR_KEY&pageurl=https://example.com'"