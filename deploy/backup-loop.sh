#!/bin/sh
set -eu
umask 077
mkdir -p /backups
while true; do
    target="/backups/fleet-$(date +%F).dump"
    if [ ! -f "$target" ]; then
        if pg_dump --format=custom --no-owner --file="$target.tmp"; then
            mv "$target.tmp" "$target"
            date +%s > /backups/last-success
            find /backups -maxdepth 1 -name 'fleet-*.dump' -type f -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete
            echo "Database backup completed: $(date -Iseconds)"
        else
            echo "Database backup failed: $(date -Iseconds)" >&2
        fi
    fi
    sleep 300
done
