# syntax=docker/dockerfile:1.7
#
# Image Cloud Sync Manager (UNRAID-001).
# Contexte de build : la racine du dépôt.
#
#   docker build -f docker/Dockerfile -t cloud-sync-manager:0.1.0 .
#
# Unraid ne tourne que sur x86_64 : une seule plateforme à produire.

# --- 1. Build du frontend ----------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Binaire rclone -------------------------------------------------------
# Version épinglée : elle est enregistrée dans chaque exécution (§14) et
# conditionne la matrice de compatibilité par fournisseur (§9.3).
# Le binaire officiel est construit avec CGO_ENABLED=0, donc statique : il
# fonctionne tel quel dans une base Debian.
FROM rclone/rclone:1.75.1 AS rclone

# --- 3. Runtime --------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# passwd fournit useradd/groupadd/usermod, utilisés par l'entrypoint pour
# aligner l'identité sur PUID/PGID ; il n'est pas garanti dans une base slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gosu passwd ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN pip install --no-cache-dir .

COPY --from=web /web/dist /app/web
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV CSM_CONFIG_DIR=/config \
    CSM_WEB_DIR=/app/web \
    CSM_PORT=3572 \
    CSM_ALLOWED_ROOTS=/mnt/user \
    PUID=99 \
    PGID=100 \
    UMASK=000

# 53682 : port sur lequel rclone conduit l'autorisation OAuth. Ouvert
# seulement le temps d'une autorisation, jamais en permanence.
EXPOSE 3572 53682
VOLUME ["/config"]

# Unraid affiche cet état dans son interface Docker.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3572/api/health', timeout=4).status == 200 else 1)"

LABEL org.opencontainers.image.title="Cloud Sync Manager" \
      org.opencontainers.image.description="Synchronisation Cloud pour Unraid, moteur rclone" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "csm.main:app", "--host", "0.0.0.0", "--port", "3572"]
