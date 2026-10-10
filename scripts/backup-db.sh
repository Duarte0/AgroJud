#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=${1:-}
OUTPUT=${2:-}
shift 2 2>/dev/null || true

case "$MODE" in
  demo)
    DEFAULT_ENV="$ROOT_DIR/.env.demo"
    DEFAULT_PROJECT=agrojud-demo
    ;;
  real)
    DEFAULT_ENV="$ROOT_DIR/.env.real"
    DEFAULT_PROJECT=agrojud-real
    ;;
  *)
    echo "Usage: $0 demo|real [archive.dump] [--env-file FILE --project NAME]" >&2
    exit 2
    ;;
esac

ENV_FILE=$DEFAULT_ENV
PROJECT=$DEFAULT_PROJECT
CUSTOM_ENV=0
CUSTOM_PROJECT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      [ "$#" -ge 2 ] || { echo "--env-file requires a path." >&2; exit 2; }
      ENV_FILE=$2
      CUSTOM_ENV=1
      shift 2
      ;;
    --project)
      [ "$#" -ge 2 ] || { echo "--project requires a name." >&2; exit 2; }
      PROJECT=$2
      CUSTOM_PROJECT=1
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ "$CUSTOM_ENV" -ne "$CUSTOM_PROJECT" ]; then
  echo "Custom source requires both --env-file and --project." >&2
  exit 2
fi
if [ "$CUSTOM_PROJECT" -eq 1 ]; then
  case "$PROJECT" in
    *[!A-Za-z0-9_-]*|'') echo "Invalid Compose project name." >&2; exit 2 ;;
  esac
  case "$MODE:$PROJECT" in
    demo:*real*|real:*demo*|real:*e2e*)
      echo "The project name does not match the selected environment." >&2
      exit 2
      ;;
  esac
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE." >&2
  exit 2
fi
case "$ENV_FILE" in
  /*) ;;
  *) ENV_FILE="$ROOT_DIR/$ENV_FILE" ;;
esac

env_value() {
  sed -n "s/^$1=//p" "$ENV_FILE" | sed -n '1p'
}

if [ "$(env_value AGROJUD_ENV)" != "$MODE" ] || \
   [ "$(env_value COMPOSE_PROJECT_NAME)" != "$PROJECT" ]; then
  echo "The environment file must declare AGROJUD_ENV=$MODE and COMPOSE_PROJECT_NAME=$PROJECT." >&2
  exit 2
fi
DATABASE=$(env_value POSTGRES_DB)
if [ -z "$DATABASE" ]; then
  echo "The environment file must declare POSTGRES_DB." >&2
  exit 2
fi
case "$MODE:$DATABASE" in
  demo:*real*|real:*demo*)
    echo "The database name conflicts with the selected source environment." >&2
    exit 2
    ;;
esac

if [ -z "$OUTPUT" ]; then
  OUTPUT="$ROOT_DIR/backups/$PROJECT-$(date -u +%Y%m%dT%H%M%SZ).dump"
elif [ "${OUTPUT#/}" = "$OUTPUT" ]; then
  OUTPUT="$ROOT_DIR/$OUTPUT"
fi
OUTPUT_DIR=${OUTPUT%/*}
MANIFEST="$OUTPUT.manifest"
mkdir -p "$OUTPUT_DIR"
if [ -e "$OUTPUT" ] || [ -e "$MANIFEST" ]; then
  echo "Refusing to overwrite an existing archive or manifest." >&2
  exit 2
fi

umask 077
ARCHIVE_TMP=$(mktemp "$OUTPUT.tmp.XXXXXX")
MANIFEST_TMP=$(mktemp "$MANIFEST.tmp.XXXXXX")
TABLE_DUMP_TMP=$(mktemp "${TMPDIR:-/tmp}/agrojud-backup-check.XXXXXX")
TABLE_DIGESTS_BEFORE=$(mktemp "${TMPDIR:-/tmp}/agrojud-backup-before.XXXXXX")
TABLE_DIGESTS_AFTER=$(mktemp "${TMPDIR:-/tmp}/agrojud-backup-after.XXXXXX")
cleanup() {
  rm -f "$ARCHIVE_TMP" "$MANIFEST_TMP" "$TABLE_DUMP_TMP" \
    "$TABLE_DIGESTS_BEFORE" "$TABLE_DIGESTS_AFTER"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

compose() {
  docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" \
    -f "$ROOT_DIR/compose.yaml" "$@"
}

collect_stats() {
  compose exec -T db sh -ec 'psql -X --set=ON_ERROR_STOP=1 --tuples-only --no-align \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' <<'SQL'
SELECT 'database=' || current_database()
UNION ALL SELECT 'alembic_revision=' || COALESCE((SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version), 'none')
UNION ALL SELECT 'processes=' || count(*)::text FROM processes
UNION ALL SELECT 'representations=' || count(*)::text FROM representations
UNION ALL SELECT 'representation_versions=' || count(*)::text FROM representation_versions
UNION ALL SELECT 'collections=' || count(*)::text FROM collections
UNION ALL SELECT 'collection_observations=' || count(*)::text FROM collection_observations
UNION ALL SELECT 'collection_results=' || count(*)::text FROM collection_results
UNION ALL SELECT 'movement_occurrences=' || count(*)::text FROM movement_occurrences
UNION ALL SELECT 'movement_snapshots=' || count(*)::text FROM movement_snapshots
UNION ALL SELECT 'quarantine_rejections=' || count(*)::text FROM quarantine_rejections
UNION ALL SELECT 'triage_decisions=' || count(*)::text FROM process_triage
UNION ALL SELECT 'triage_history=' || count(*)::text FROM process_triage_history
UNION ALL SELECT 'watchlist_entries=' || count(*)::text FROM process_watchlist_entries
UNION ALL SELECT 'watchlist_history=' || count(*)::text FROM process_watchlist_history
UNION ALL SELECT 'jobs=' || count(*)::text FROM jobs
UNION ALL SELECT 'job_attempts=' || count(*)::text FROM job_attempts
UNION ALL SELECT 'job_checkpoints=' || count(*)::text FROM job_checkpoints
UNION ALL SELECT 'process_news=' || count(*)::text FROM process_news
UNION ALL SELECT 'process_signals=' || count(*)::text FROM process_signals
UNION ALL SELECT 'database_bytes=' || pg_database_size(current_database())::text;
SQL
}

stats_value() {
  printf '%s\n' "$1" | sed -n "s/^$2=//p" | sed -n '1p'
}

table_digest() {
  TABLE_NAME=$1
  ORDER_COLUMN=$2
  case "$TABLE_NAME:$ORDER_COLUMN" in
    processes:id|representations:id|representation_versions:id|collections:id|\
    representation_subjects:id|collection_observations:id|collection_results:id|\
    movement_occurrences:id|movement_snapshots:id|movement_snapshot_occurrences:id|\
    quarantine_rejections:id|quarantine_resolutions:id|process_triage:process_id|\
    process_triage_history:id|process_watchlist_entries:process_id|\
    process_watchlist_history:id|process_watch_cycles:id|\
    representation_watch_baselines:id|process_news:id|jobs:id|job_attempts:id|\
    job_events:id|job_checkpoints:id|saved_searches:id|saved_search_versions:id|\
    schedule_dispatches:id|queue_claim_state:key|source_rate_limits:source|\
    signal_run_processes:id|signal_run_inputs:id|process_signals:id|signal_evaluations:id) ;;
    *) echo "Internal table digest allowlist rejected $TABLE_NAME." >&2; return 2 ;;
  esac
  compose exec -T db sh -ec "psql -X --set=ON_ERROR_STOP=1 --tuples-only --no-align \\
    --username \"\$POSTGRES_USER\" --dbname \"\$POSTGRES_DB\" \\
    --command 'SELECT to_jsonb(row_data)::text FROM (SELECT * FROM $TABLE_NAME ORDER BY $ORDER_COLUMN) AS row_data'" \
    > "$TABLE_DUMP_TMP"
  sha256sum "$TABLE_DUMP_TMP" | awk '{print $1}'
}

STATS_BEFORE=$(collect_stats)
if [ "$(stats_value "$STATS_BEFORE" database)" != "$DATABASE" ]; then
  echo "The selected Compose database does not match POSTGRES_DB." >&2
  exit 1
fi
REVISION=$(stats_value "$STATS_BEFORE" alembic_revision)
if [ -z "$REVISION" ] || [ "$REVISION" = none ]; then
  echo "The source database has no Alembic revision; apply migrations before backing it up." >&2
  exit 1
fi
digest_tables() {
  TARGET_FILE=$1
  : > "$TARGET_FILE"
  for TABLE in processes representations representation_versions representation_subjects \
    collections collection_observations collection_results movement_occurrences movement_snapshots \
    movement_snapshot_occurrences quarantine_rejections quarantine_resolutions process_triage \
    process_triage_history process_watchlist_entries process_watchlist_history process_watch_cycles \
    representation_watch_baselines process_news jobs job_attempts job_events job_checkpoints \
    saved_searches saved_search_versions schedule_dispatches queue_claim_state source_rate_limits \
    signal_run_processes signal_run_inputs process_signals signal_evaluations; do
    case "$TABLE" in
      process_triage|process_watchlist_entries) ORDER_COLUMN=process_id ;;
      queue_claim_state) ORDER_COLUMN=key ;;
      source_rate_limits) ORDER_COLUMN=source ;;
      *) ORDER_COLUMN=id ;;
    esac
    DIGEST=$(table_digest "$TABLE" "$ORDER_COLUMN") || return 1
    [ -n "$DIGEST" ] || { echo "Could not calculate the $TABLE digest." >&2; return 1; }
    printf '%s_sha256=%s\n' "$TABLE" "$DIGEST" >> "$TARGET_FILE"
  done
}

digest_tables "$TABLE_DIGESTS_BEFORE"

compose exec -T db sh -ec 'exec pg_dump --format=custom --no-owner --no-privileges \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' > "$ARCHIVE_TMP"
compose exec -T db pg_restore --list < "$ARCHIVE_TMP" >/dev/null
STATS_AFTER=$(collect_stats)
for KEY in database alembic_revision processes representations representation_versions collections \
  collection_observations collection_results movement_occurrences movement_snapshots \
  quarantine_rejections triage_decisions triage_history watchlist_entries watchlist_history \
  jobs job_attempts job_checkpoints process_news process_signals; do
  if [ "$(stats_value "$STATS_BEFORE" "$KEY")" != "$(stats_value "$STATS_AFTER" "$KEY")" ]; then
    echo "The source changed while the backup was being checked; retry during a quiet window." >&2
    exit 1
  fi
done
digest_tables "$TABLE_DIGESTS_AFTER"
if ! cmp -s "$TABLE_DIGESTS_BEFORE" "$TABLE_DIGESTS_AFTER"; then
  echo "Selected source rows changed during backup verification; retry during a quiet window." >&2
  exit 1
fi

ARCHIVE_SHA=$(sha256sum "$ARCHIVE_TMP" | awk '{print $1}')
CREATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
{
  printf '%s\n' 'format=agrojud-postgres-custom-v1'
  printf 'environment=%s\nproject=%s\ncreated_at=%s\nsha256=%s\n' \
    "$MODE" "$PROJECT" "$CREATED_AT" "$ARCHIVE_SHA"
  printf '%s\n' "$STATS_BEFORE"
  cat "$TABLE_DIGESTS_BEFORE"
} > "$MANIFEST_TMP"
chmod 600 "$ARCHIVE_TMP" "$MANIFEST_TMP"
mv "$ARCHIVE_TMP" "$OUTPUT"
mv "$MANIFEST_TMP" "$MANIFEST"

printf 'Backup verified: environment=%s project=%s database=%s revision=%s\n' \
  "$MODE" "$PROJECT" "$DATABASE" "$REVISION"
printf 'Archive: %s\nManifest: %s\n' "$OUTPUT" "$MANIFEST"
printf 'Rows: processes=%s representations=%s versions=%s triage=%s watchlist=%s jobs=%s\n' \
  "$(stats_value "$STATS_BEFORE" processes)" \
  "$(stats_value "$STATS_BEFORE" representations)" \
  "$(stats_value "$STATS_BEFORE" representation_versions)" \
  "$(stats_value "$STATS_BEFORE" triage_decisions)" \
  "$(stats_value "$STATS_BEFORE" watchlist_entries)" \
  "$(stats_value "$STATS_BEFORE" jobs)"
