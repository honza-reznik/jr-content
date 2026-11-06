FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN rm -f /etc/apt/apt.conf.d/docker-clean
RUN --mount=type=cache,id=jrc-bb-apt,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=jrc-bb-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get -o Binary::apt::APT::Keep-Downloaded-Packages=true install -y --no-install-recommends \
    libpq-dev gcc curl ffmpeg

# Install Node.js for Tailwind build
RUN --mount=type=cache,id=jrc-bb-apt,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=jrc-bb-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    printf 'Binary::apt::APT::Keep-Downloaded-Packages "true";\n' > /etc/apt/apt.conf.d/99-jrc-cache \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get -o Binary::apt::APT::Keep-Downloaded-Packages=true install -y nodejs

COPY requirements.txt .
RUN --mount=type=cache,id=jrc-bb-pip,target=/root/.cache/pip pip install -r requirements.txt
COPY requirements-jrc.txt .
RUN --mount=type=cache,id=jrc-bb-pip,target=/root/.cache/pip pip install -r requirements-jrc.txt

# JR Content F4-B01: stejné serverové schéma Markdownu jako v editoru, dostupné i pod bind mountem.
COPY apps/jrc_texts/editor/package*.json /opt/jrc-texts/
RUN --mount=type=cache,id=jrc-npm,target=/root/.npm \
    npm ci --prefix /opt/jrc-texts --ignore-scripts --no-audit --no-fund
COPY theme/static_src/package*.json /app/theme/static_src/
RUN --mount=type=cache,id=jrc-npm,target=/root/.npm \
    cd theme/static_src && npm ci --no-audit --no-fund
COPY apps/jrc_texts/editor/*.mjs /opt/jrc-texts/
RUN --network=none npm run build --prefix /opt/jrc-texts

COPY --exclude=.jrc-source.tar.gz . .
RUN mkdir -p apps/jrc_texts/static/jrc_texts/dist \
    && cp /opt/jrc-texts/script.js apps/jrc_texts/static/jrc_texts/dist/script.js

# Build Tailwind CSS
RUN --network=none cd theme/static_src && npm run build

# Collect static files
RUN --network=none DJANGO_SETTINGS_MODULE=config.settings.production \
    SECRET_KEY=build-placeholder \
    DATABASE_URL=sqlite:///tmp/build.db \
    python manage.py collectstatic --noinput

# Verze ani archiv nesmějí zneplatnit vrstvy závislostí a statických souborů.
ARG JRC_SOURCE_VERSION=unknown
COPY .jrc-source.tar.gz /tmp/jrc-source.tar.gz
RUN mkdir -p /opt/jrc-source \
    && mv /tmp/jrc-source.tar.gz /opt/jrc-source/jr-content-${JRC_SOURCE_VERSION}.tar.gz

EXPOSE 8000

# One worker, threaded. Importing this app costs ~100 MB before serving a
# single request, so two workers alone exceeded a 512 MB dyno's quota and the
# whole thing ran in swap. Raise --workers only alongside the container memory
# to match (budget ~250 MB per worker).
#
# Deliberately NO --max-requests. gthread sets ``alive = False`` at the START of
# the request that trips the counter, which stops the heartbeat while that
# request is still running — the arbiter then SIGKILLs the worker after
# ``timeout`` (30s) and the client gets a dropped connection. Verified against
# gunicorn 22: a 12s request that tripped --max-requests was killed, while the
# same request without it returned 200. Uploads here are allowed up to 1 GB, so
# no fixed timeout makes recycling safe. The RSS ratchet it was guarding against
# is fixed at the source instead (AWS_S3_MAX_MEMORY_SIZE + streaming reads).
CMD gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4
