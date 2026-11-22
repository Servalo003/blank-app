#!/usr/bin/env bash
# One-time setup of Deriv Signal Lab on an Ubuntu VM (tested target: Oracle Cloud Always Free).
# Usage, from the repository root:  sudo deploy/setup-oracle.sh your-name.duckdns.org
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Usage: sudo deploy/setup-oracle.sh your-name.duckdns.org" >&2
  exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo." >&2
  exit 1
fi
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml)

echo "==> 1/6 Swap (only on small machines)"
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 3000000 ] && ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "    added 2 GB swap"
fi

echo "==> 2/6 Docker"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -q
  apt-get install -y -q ca-certificates curl git openssl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker >/dev/null

echo "==> 3/6 Host firewall: allow HTTP/HTTPS"
# Oracle's Ubuntu images ship iptables rules that reject everything except SSH.
for port in 80 443; do
  if ! iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    reject_line=$(iptables -L INPUT --line-numbers -n | awk '/REJECT/ {print $1; exit}')
    if [ -n "$reject_line" ]; then
      iptables -I INPUT "$reject_line" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    else
      iptables -A INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    fi
  fi
done
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save >/dev/null 2>&1 || true
fi

echo "==> 4/6 Configuration (.env)"
set_kv() {  # set_kv KEY VALUE  (values here are alphanumeric / URL-safe)
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}
rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-"$1"; }
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  set_kv APP_ACCESS_TOKEN "$(rand 40)"
  set_kv POSTGRES_PASSWORD "$(rand 32)"
  echo "    created .env with new random secrets"
fi
set_kv APP_ENV production
set_kv DOMAIN "$DOMAIN"
set_kv CORS_ORIGINS "https://$DOMAIN"
set_kv PUBLIC_APP_URL "https://$DOMAIN"
set_kv GIT_SHA "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

echo "==> 5/6 Build"
"${COMPOSE[@]}" build
if ! grep -q '^VAPID_PRIVATE_KEY=.\+' .env; then
  keys=$("${COMPOSE[@]}" run --rm --no-deps -T api python -m app.tools.vapid)
  set_kv VAPID_PUBLIC_KEY "$(echo "$keys" | sed -n 's/^VAPID_PUBLIC_KEY=//p')"
  set_kv VAPID_PRIVATE_KEY "$(echo "$keys" | sed -n 's/^VAPID_PRIVATE_KEY=//p')"
  echo "    generated Web Push (VAPID) keys"
fi

echo "==> 6/6 Start"
"${COMPOSE[@]}" up -d
install -m 0755 deploy/backup.sh /usr/local/bin/dsl-backup
echo "0 3 * * * root cd $(pwd) && /usr/local/bin/dsl-backup >/dev/null 2>&1" > /etc/cron.d/dsl-backup

echo "    waiting for https://$DOMAIN (certificate can take a minute)..."
for _ in $(seq 1 30); do
  if curl -fsS -m 5 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 5
done

echo
if [ "${ok:-0}" = 1 ]; then
  echo "Deriv Signal Lab is running at  https://$DOMAIN"
else
  echo "The app started, but https://$DOMAIN is not answering yet."
  echo "Check: DNS points to this VM, ports 80/443 are open in the Oracle security list,"
  echo "and logs with:  sudo docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs caddy"
fi
echo
echo "Your access token (paste it into the app; keep it private):"
grep '^APP_ACCESS_TOKEN=' .env | cut -d= -f2-
