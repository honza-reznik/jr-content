FROM node:24-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    NODE_PATH=/opt/jrc-tools/node_modules \
    PATH=/opt/jrc-tools/node_modules/.bin:$PATH
RUN printf 'Acquire::Retries "3";\n' > /etc/apt/apt.conf.d/80-jrc-retries \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git make ffmpeg python3 python3-venv python3-pip \
    python3-boto3 postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --prefix /opt/jrc-tools --no-audit --no-fund playwright@1.64.0 \
    && rm -rf /root/.npm
RUN /opt/jrc-tools/node_modules/.bin/playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright-browsers \
    && rm -rf /var/lib/apt/lists/* /root/.npm
WORKDIR /workspace
CMD ["sleep", "infinity"]
