# Sicherungs-Container: PostgreSQL-17-Werkzeuge (pg_dump/pg_restore) + rclone/rsync für externe Kopien
FROM postgres:17
RUN apt-get update && apt-get install -y --no-install-recommends \
        rclone rsync openssh-client ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/gemeinsam.sh scripts/backup.sh scripts/restore.sh backup/backup-dienst.sh /skripte/
RUN chmod +x /skripte/*.sh
ENTRYPOINT []
CMD ["/skripte/backup-dienst.sh"]
