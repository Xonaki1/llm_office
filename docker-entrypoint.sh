#!/usr/bin/env bash
# Container entrypoint. `api` serves HTTP, `worker` runs agent workflows,
# `migrate` applies migrations and exits.
set -euo pipefail

wait_for() {
  local name="$1" host="$2" port="$3" attempts="${4:-60}"
  for _ in $(seq 1 "$attempts"); do
    if (echo > "/dev/tcp/${host}/${port}") >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "timed out waiting for ${name} at ${host}:${port}" >&2
  return 1
}

# Parse host:port out of the connection URLs so the container waits for its
# dependencies instead of crash-looping on startup.
if [[ -n "${DATABASE_URL:-}" ]]; then
  db_hostport="$(sed -E 's#.*@([^/?]+).*#\1#' <<< "$DATABASE_URL")"
  wait_for postgres "${db_hostport%%:*}" "${db_hostport##*:}" || exit 1
fi

if [[ -n "${REDIS_URL:-}" ]]; then
  redis_hostport="$(sed -E 's#redis://([^/?]+).*#\1#' <<< "$REDIS_URL")"
  redis_host="${redis_hostport%%:*}"
  redis_port="${redis_hostport##*:}"
  [[ "$redis_port" == "$redis_host" ]] && redis_port=6379
  wait_for redis "$redis_host" "$redis_port" || exit 1
fi

case "${1:-api}" in
  api)
    # One process applies migrations; RUN_MIGRATIONS is set on exactly one
    # service in the compose file so concurrent replicas cannot race.
    if [[ "${RUN_MIGRATIONS:-false}" == "true" ]]; then
      echo "applying migrations"
      alembic upgrade head
    fi
    exec gunicorn api.main:app --config gunicorn.conf.py
    ;;
  worker)
    exec arq worker.main.WorkerSettings
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
