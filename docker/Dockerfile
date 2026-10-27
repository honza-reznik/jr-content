FROM node:24-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    NODE_PATH=/opt/jrc-tools/node_modules \
    PATH=/opt/jrc-tools/node_modules/.bin:$PATH
RUN printf 'Acquire::Retries "3";\n' > /etc/apt/apt.conf.d/80-jrc-retries \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates age git make ffmpeg python3 python3-venv python3-pip \
    python3-boto3 postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --prefix /opt/jrc-tools --no-audit --no-fund playwright@1.64.0 \
    && rm -rf /root/.npm
RUN /opt/jrc-tools/node_modules/.bin/playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright-browsers \
    && rm -rf /var/lib/apt/lists/* /root/.npm
# Playwright MCP pro vizuální testery (F0-B04): prohlížeč běží jen tady v kontejneru.
RUN npm install --prefix /opt/jrc-mcp --no-audit --no-fund @playwright/mcp@0.0.83 \
    && rm -rf /root/.npm
# F2-B09a: klient PostgreSQL 18 (shodný se serverem 18) z oficiálního repozitáře PGDG. Klíč se ověří pevným otiskem
# ještě před přidáním repozitáře (B97B 0AFC AA1A 47F0 44F2 44A0 7FCC 7D46 ACCC 4CF8) a balíčky ověřuje apt podpisem.
# Vrstva je až za těžkými vrstvami (Chromium), aby se ty nestahovaly znovu; klient 15 z Debianu se odstraní
# a `curl` s `gnupg` (jen pomocné nástroje pro stažení a ověření klíče) se ve stejné vrstvě zase odinstalují.
RUN set -eu; \
    apt-get update && apt-get install -y --no-install-recommends curl gnupg; \
    curl -fsSL --retry 3 -o /tmp/pgdg.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    fingerprint=$(gpg --show-keys --with-colons /tmp/pgdg.asc | awk -F: '$1 == "fpr" {print $10; exit}'); \
    [ "$fingerprint" = B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8 ] || { echo "Neplatný otisk klíče PGDG: $fingerprint" >&2; exit 1; }; \
    gpg --dearmor < /tmp/pgdg.asc > /usr/share/keyrings/pgdg.gpg; rm /tmp/pgdg.asc; \
    echo 'deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get purge -y postgresql-client postgresql-client-15; \
    apt-get install -y --no-install-recommends postgresql-client-18; \
    apt-get purge -y --auto-remove curl gnupg; \
    psql --version | grep -q 'psql (PostgreSQL) 18\.'; \
    hash -r; \
    if command -v curl >/dev/null || command -v gpg >/dev/null; then echo "curl nebo gpg zůstaly v obrazu: $(command -v curl) $(command -v gpg)" >&2; exit 1; fi; \
    rm -rf /var/lib/apt/lists/*
COPY jrc-playwright-mcp /usr/local/bin/jrc-playwright-mcp
WORKDIR /workspace
CMD ["sleep", "infinity"]
