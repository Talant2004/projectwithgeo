#!/bin/bash
# Публикует текущий адрес временного туннеля Cloudflare в ветку server-url репозитория:
# приложение читает оттуда server_url.json и подключается само.
# Запускается из cron каждые 2 минуты (пушит, только когда адрес изменился)
# и кнопкой в программе загрузки: с --force публикует адрес в любом случае.
# Если туннель не работает, перезапускает его.
set -euo pipefail

REPO="${POVILIKA_URL_REMOTE:-git@github.com:Talant2004/geoserver.git}"
BRANCH=server-url
WORK="${POVILIKA_URL_WORKDIR:-/data/povilika/url-publish}"
CONTAINER=povilika-quicktunnel
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

mkdir -p "$WORK"
exec 9>"$WORK/lock"
flock -w 120 9 || { echo "$(date -Is) уже выполняется другой публикацией"; exit 1; }

say() { echo "$(date -Is) $*"; }

tunnel_url() {
  docker logs "$CONTAINER" 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' |
    grep -v '^https://api\.' | tail -1 || true
}

# Ждёт, пока туннель выдаст адрес, отличный от $1.
wait_new_url() {
  local old=$1 u
  for _ in $(seq 30); do
    sleep 2
    u=$(tunnel_url)
    if [ -n "$u" ] && [ "$u" != "$old" ]; then echo "$u"; return 0; fi
  done
  return 1
}

fresh=0  # туннель только что запущен: адрес ещё подключается, проверять рано
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  say "туннеля нет, создаю"
  docker run -d --name "$CONTAINER" --restart unless-stopped --network server_default \
    cloudflare/cloudflared:latest tunnel --no-autoupdate --protocol http2 --url http://gateway:80 >/dev/null
  wait_new_url "" >/dev/null || true
  fresh=1
elif [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != true ]; then
  say "туннель остановлен, запускаю"
  old=$(tunnel_url)
  docker start "$CONTAINER" >/dev/null
  wait_new_url "$old" >/dev/null || true
  fresh=1
fi

url=$(tunnel_url)
[ -n "$url" ] || { say "туннель $CONTAINER не дал адреса"; exit 1; }

# После обрыва интернета Cloudflare удаляет временный туннель, а cloudflared бесконечно
# переподключается к нему («Tunnel not found»), и адрес перестаёт открываться.
# Лечится только перезапуском: туннель получит новый адрес.
broken=""
if [ "$fresh" = 1 ]; then
  :
elif docker logs --since 3m "$CONTAINER" 2>&1 | grep -q 'Tunnel not found'; then
  broken="Cloudflare удалил туннель"
else
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$url/" || true)
  # 000 — адрес не находится или не отвечает, 530 — туннель не подключён.
  # Любой другой ответ (даже 502 при выключенном GeoServer) значит, что туннель жив.
  if [ "$code" = 000 ] || [ "$code" = 530 ]; then
    if curl -s -o /dev/null -m 10 https://www.cloudflare.com/; then
      broken="адрес не открывается (код $code)"
    else
      say "нет интернета, туннель не трогаю"
      exit 1
    fi
  fi
fi
if [ -n "$broken" ]; then
  say "$broken — перезапускаю туннель"
  docker restart "$CONTAINER" >/dev/null
  url=$(wait_new_url "$url") || { say "туннель не выдал новый адрес за минуту"; exit 1; }
  say "новый адрес туннеля $url"
fi
url="$url/geoserver"

if [ "$FORCE" = 0 ] && [ "$(cat "$WORK/published" 2>/dev/null)" = "$url" ]; then
  exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
git init -q -b "$BRANCH"
git config user.name "Talant2004"
git config user.email "Talant2004@users.noreply.github.com"
printf '{\n  "url": "%s",\n  "updated": "%s"\n}\n' "$url" "$(date -Is)" > server_url.json
git add server_url.json
git commit -q -m "Адрес сервера: $url"
GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=20" git push -q -f "$REPO" "$BRANCH:$BRANCH"
echo "$url" > "$WORK/published"
say "опубликован $url"
