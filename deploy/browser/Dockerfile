FROM --platform=linux/arm64 docker.io/library/node:22.22.0-bookworm-slim@sha256:5e22e6fb4448236070fdb260662e49fd58779876855baf95388534475c3dcd11
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /opt/browser
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev --no-audit --no-fund \
    && npx playwright install --with-deps chromium \
    && apt-get update && apt-get install -y --no-install-recommends iptables \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright /opt/browser
COPY *.mjs network-policy.sh ./
RUN mkdir -p /opt/browser/frozen /opt/browser/output /home/browser \
    && chown -R 10001:10001 /home/browser \
    && chmod a+rX /opt/browser/*.mjs /opt/browser/network-policy.sh
USER 10001:10001
CMD ["node", "/opt/browser/inspector.mjs"]
