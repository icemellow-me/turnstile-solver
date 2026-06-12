#!/bin/bash
# ============================================================
# Solver Auto-Install & Auto-Start for hermes container
# Target: 172.17.0.2 (ports 8866 reCAPTCHA, 8877 Turnstile)
# Usage:
#   HERMES_CONTAINER=hermes API_KEY=your_key bash install-solvers.sh
#   docker exec hermes bash -c 'API_KEY=key bash /tmp/install-solvers.sh'
# ============================================================
set -e

HERMES_CONTAINER="${HERMES_CONTAINER:-hermes}"
HERMES_IP="172.17.0.2"
TPORT=8877   # Turnstile
RPORT=8866   # reCAPTCHA

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }

# ── API Key ──────────────────────────────────────────────────
if [ -z "$API_KEY" ]; then
  echo -e "${RED}[ERROR]${NC} API_KEY not set. Run with: API_KEY=your_key bash install-solvers.sh"
  exit 1
fi

info "Starting install in hermes container (target: $HERMES_IP)"

# ── Step 1: System deps ──────────────────────────────────────
info "Step 1/6 — System dependencies..."
docker exec "$HERMES_CONTAINER" bash -c "
  export DEBIAN_FRONTEND=noninteractive
  dpkg -l git >/dev/null 2>&1 || apt-get install -y -qq git
  dpkg -l python3 >/dev/null 2>&1 || apt-get install -y -qq python3 python3-pip
  dpkg -l xvfb >/dev/null 2>&1 || apt-get install -y -qq xvfb
"
echo -e "${GREEN}✅${NC} System deps ready"

# ── Step 2: Clone / update repos ────────────────────────────
info "Step 2/6 — Cloning solver repos..."
docker exec "$HERMES_CONTAINER" bash -c "
  export GIT_TERMINAL_PROMPT=0 DEBIAN_FRONTEND=noninteractive
  cd /opt
  [ -d turnstile-solver ]    || git clone -q https://github.com/icemellow-me/turnstile-solver.git
  [ -d recaptcha-v2-solver ] || git clone -q https://github.com/icemellow-me/recaptcha-v2-solver.git
"
echo -e "${GREEN}✅${NC} Repos ready"

# ── Step 3: Python deps ──────────────────────────────────────
info "Step 3/6 — Python dependencies..."
docker exec "$HERMES_CONTAINER" bash -c "
  pip install -q --break-system-packages fastapi uvicorn websockets pydantic httpx 2>/dev/null
  pip install -q --break-system-packages playwright 2>/dev/null
  python3 -m playwright install chromium 2>&1 | tail -3
"
echo -e "${GREEN}✅${NC} Python deps ready"

# ── Step 4: Kill old instances ───────────────────────────────
info "Step 4/6 — Stopping old solvers..."
docker exec "$HERMES_CONTAINER" bash -c "
  pkill -f 'solver-server.py' 2>/dev/null || true
  pkill -f Xvfb 2>/dev/null || true
  sleep 2
"
echo -e "${GREEN}✅${NC} Old instances cleared"

# ── Step 5: Start Xvfb + solvers ────────────────────────────
info "Step 5/6 — Starting servers on $HERMES_IP (ports $TPORT & $RPORT)..."

# Start Xvfb (headless display for Playwright/Chrome)
docker exec -d "$HERMES_CONTAINER" bash -c "pgrep Xvfb >/dev/null || Xvfb :99 -screen 0 1280x720x24 &"
sleep 2

# Start Turnstile solver
docker exec -d "$HERMES_CONTAINER" bash -c "
  export DISPLAY=:99
  cd /opt/turnstile-solver
  nohup python3 solver-server.py --api-key $API_KEY --port $TPORT >/tmp/turnstile.log 2>&1 &
  echo \"Turnstile PID: \$!\"
"

# Start reCAPTCHA solver
docker exec -d "$HERMES_CONTAINER" bash -c "
  export DISPLAY=:99
  cd /opt/recaptcha-v2-solver
  nohup python3 solver-server.py --api-key $API_KEY --port $RPORT >/tmp/recaptcha.log 2>&1 &
  echo \"reCAPTCHA PID: \$!\"
"

sleep 3

# ── Step 6: Verify ──────────────────────────────────────────
info "Step 6/6 — Verifying..."
sleep 5

for svc_port in "Turnstile:$TPORT" "reCAPTCHA:$RPORT"; do
  name="${svc_port%:*}"; port="${svc_port#*:}"
  if docker exec "$HERMES_CONTAINER" bash -c "curl -sf http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ $name${NC} ($port) — HEALTHY"
  else
    echo -e "${RED}❌ $name${NC} ($port) — NOT responding"
    echo "   Logs: docker exec $HERMES_CONTAINER tail -20 /tmp/${name,,}.log"
  fi
done

echo ""
info "Done!"
echo ""
echo "Logs:"
echo "  docker exec $HERMES_CONTAINER tail -20 /tmp/turnstile.log"
echo "  docker exec $HERMES_CONTAINER tail -20 /tmp/recaptcha.log"
echo ""
echo "Test:"
echo "  curl -X POST http://\$HOST:8866/in.php -d 'key=\$API_KEY\&method=userrecaptcha\&googlekey=6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI\&pageurl=https://www.google.com/recaptcha/api2/demo'"