#!/bin/bash
# Solver Test Suite — run against live solver endpoints on EC2
# Usage: bash test-solvers.sh [EC2_IP]

SOLVER_HOST="${1:-23.22.196.74}"
RECAPTCHA_URL="http://${SOLVER_HOST}:8866"
TURNSTILE_URL="http://${SOLVER_HOST}:8877"
API_KEY="8010000000ccojr5nrbg516w5jvw1wu9"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo "========================================"
echo " Solver Test Suite"
echo " EC2: $SOLVER_HOST"
echo "========================================"

wait_for_token() {
  local id="$1"; local url="$2"; local name="$3"; local max_wait=60
  echo "⏳ Waiting for ${name} token (ID: $id)..."
  for i in $(seq 1 $max_wait); do
    sleep 3
    resp=$(curl -s "${url}/res.php?key=${API_KEY}&action=get&id=${id}")
    echo "$resp" | grep -q "OK\|ERROR" && echo "$resp" && return 0
    echo "  [$i/$max_wait] still waiting..."
  done
  echo -e "${RED}❌ Timeout${NC}"; return 1
}

# TEST 1 — reCAPTCHA v2
echo -e "\n${YELLOW}[TEST 1]${NC} reCAPTCHA v2 — Google test key (always passes)"
START=$(date +%s)
RESP=$(curl -s -X POST "${RECAPTCHA_URL}/in.php" \
  -d "key=${API_KEY}&method=userrecaptcha&googlekey=6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI&pageurl=https://www.google.com/recaptcha/api2/demo")
echo "Submit: $RESP"
if echo "$RESP" | grep -q "^[0-9]"; then
  ID=$(echo "$RESP" | tr -d '\r\n ')
  wait_for_token "$ID" "$RECAPTCHA_URL" "reCAPTCHA v2"
  echo -e "${GREEN}✅ reCAPTCHA done in $(( $(date +%s) - START ))s${NC}"
fi

# TEST 2 — Turnstile
echo -e "\n${YELLOW}[TEST 2]${NC} Turnstile — Cloudflare test key"
START=$(date +%s)
RESP=$(curl -s -X POST "${TURNSTILE_URL}/in.php" \
  -d "key=${API_KEY}&method=turnstile&sitekey=0x4AAAAAAAD5LV2m1Xx1iA1N&pageurl=https://challenges.cloudflare.com/cdn-cgi/arena/enter")
echo "Submit: $RESP"
if echo "$RESP" | grep -q "^[0-9]"; then
  ID=$(echo "$RESP" | tr -d '\r\n ')
  wait_for_token "$ID" "$TURNSTILE_URL" "Turnstile"
  echo -e "${GREEN}✅ Turnstile done in $(( $(date +%s) - START ))s${NC}"
fi

# TEST 3 — Health
echo -e "\n${YELLOW}[TEST 3]${NC} Health checks"
for name_url in "reCAPTCHA:${RECAPTCHA_URL}" "Turnstile:${TURNSTILE_URL}"; do
  name="${name_url%:*}"; url="${name_url#*:}"
  curl -sf "${url}/health" >/dev/null 2>&1 \
    && echo -e "${GREEN}✅ $name health — UP${NC}" \
    || echo -e "${RED}❌ $name health — DOWN${NC}"
done

# TEST 4 — FlareSolverr /v1 (Turnstile)
echo -e "\n${YELLOW}[TEST 4]${NC} FlareSolverr /v1 API (Turnstile)"
RESP=$(curl -s -X POST "${TURNSTILE_URL}/v1" \
  -H "Content-Type: application/json" \
  -d '{"cmd":"request.get","url":"https://nowsecure.nl","maxTimeout":60000}')
echo "${RESP:0:300}"
echo "$RESP" | grep -q "cf_clearance\|token\|success" \
  && echo -e "${GREEN}✅ FlareSolverr /v1 — WORKING${NC}" \
  || echo -e "${YELLOW}⚠️  FlareSolverr /v1 — check response${NC}"

echo -e "\n========================================"
echo "Done!"
