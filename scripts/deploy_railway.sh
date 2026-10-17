#!/usr/bin/env bash
# Deploy the Till on Nemotron demo to Railway (new project "till-nebius", service "till"), from this directory.
# Run ONLY after Von approves hosting. Never touches any other Railway project (other Till entries are frozen).
#
#   bash scripts/deploy_railway.sh          dry run: checks + prints commands
#   bash scripts/deploy_railway.sh --run    deploy (requires the FAOS harness gate to print CONSISTENT)
#
# Uses the Dockerfile via railway.json; health check /agent/status. NEBIUS_API_KEY and TAVILY_API_KEY go from .env
# to Railway over stdin, never on the command line. The hackathon requires a runtime Token Factory call, so the
# hosted demo does spend Builder Program credits; check the Token Factory balance before judging starts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
RUN=0; [[ "${1:-}" == "--run" ]] && RUN=1
run() { printf '  $ %s\n' "$*"; if [[ $RUN == 1 ]]; then "$@"; fi; }
die() { echo "ABORT: $*"; exit 1; }
envval() { [[ -f .env ]] && grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '"' || true; }

echo "== checks"
command -v railway >/dev/null || die "railway CLI missing (brew install railway)"
railway whoami >/dev/null 2>&1 || die "railway CLI not logged in"
[[ -f railway.json && -f Dockerfile ]] || die "railway.json or Dockerfile missing"
git check-ignore -q .env || die ".env not gitignored (railway up uploads files that are not ignored)"
for k in NEBIUS_API_KEY TAVILY_API_KEY; do
  [[ -n "$(envval $k)" ]] && echo "  ok  $k found in .env (not printed)" \
                          || echo "  WARN no $k in .env: that part of the demo would run in labeled mock mode"
done

echo "== global deploy gate: bash ~/sandbox-harness/tests/faos.sh must return CONSISTENT"
if [[ $RUN == 1 ]]; then
  rc=0; out="$(bash ~/sandbox-harness/tests/faos.sh 2>&1)" || rc=$?
  echo "$out" | tail -5
  # faos.sh exits 0 = CONSISTENT, 3 = CONSISTENT_WITH_WARNINGS, 1 = INCONSISTENT. Only 0 passes unattended.
  [[ $rc == 0 ]] || die "faos.sh exit $rc, not CONSISTENT (3 = warnings: Von decides)"
else
  echo "  $ bash ~/sandbox-harness/tests/faos.sh   (runs on --run; abort unless CONSISTENT)"
fi

[[ $RUN == 0 ]] && echo "== DRY RUN: nothing below is executed"
echo "== create project + service (skip if .railway link already points at till-nebius)"
if ! railway status 2>/dev/null | grep -qi "till-nebius"; then
  run railway init --name till-nebius
  run railway add --service till
fi
for k in NEBIUS_API_KEY TAVILY_API_KEY; do
  v="$(envval $k)"
  if [[ -n "$v" ]]; then
    printf '  $ railway variable set %s --stdin --service till --skip-deploys   (value from .env via stdin)\n' "$k"
    [[ $RUN == 1 ]] && printf '%s' "$v" | railway variable set "$k" --stdin --service till --skip-deploys
  fi
done
run railway up --service till --detach
run railway domain --service till
echo "== then verify (Claude): curl -fsS https://<domain>/agent/status  and  .venv/bin/python tests/test_protocol.py https://<domain>/mcp"
echo "   If builds fail with 'secret RAILWAY_GIT_REPO_OWNER not found', connect the GitHub repo:"
echo "   railway service source connect --repo ydescieux2-del/till-nebius --branch main --service till"
