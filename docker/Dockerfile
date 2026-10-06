# syntax=docker/dockerfile:1
ARG TARGETARCH
FROM ubuntu:24.04 AS download
ARG TARGETARCH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
COPY download.sh /usr/local/bin/download-chromix
RUN /bin/sh /usr/local/bin/download-chromix "$TARGETARCH" /opt

FROM ubuntu:24.04 AS runtime
LABEL org.opencontainers.image.title="Chromix" \
      org.opencontainers.image.description="Non-root Chromix browser CLI from verified Linux release bundles" \
      org.opencontainers.image.source="https://github.com/xiaozhou26/Chromix" \
      org.opencontainers.image.licenses="BSD-3-Clause"
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates fontconfig fonts-liberation \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
        libcairo2 libcups2t64 libdbus-1-3 libdrm2 libexpat1 libgbm1 \
        libglib2.0-0t64 libgtk-3-0t64 libnspr4 libnss3 libpango-1.0-0 \
        libudev1 libvulkan1 libx11-6 libxcb1 libxcomposite1 libxdamage1 \
        libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 chromix \
    && useradd --uid 10001 --gid chromix --create-home --shell /bin/sh chromix \
    && install -d -o chromix -g chromix /home/chromix/.cache /home/chromix/.config /work
COPY --from=download /opt/chromix /opt/chromix
COPY --chmod=0755 entrypoint.sh /usr/local/bin/chromix
# Use the unprivileged user-namespace sandbox, not a setuid helper.
RUN chown -R root:root /opt/chromix \
    && chmod 0755 /opt/chromix/chromix /opt/chromix/chrome \
        /opt/chromix/chrome-sandbox /opt/chromix/chrome_crashpad_handler \
    && ln -s /opt/chromix/chromix /usr/local/bin/chromix-browser
ENV HOME=/home/chromix \
    XDG_CACHE_HOME=/home/chromix/.cache \
    XDG_CONFIG_HOME=/home/chromix/.config \
    LANG=C.UTF-8
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/chromix"]
CMD ["--version"]

FROM runtime AS runtime-amd64
LABEL org.opencontainers.image.version="154.0.8037.97"

FROM runtime AS runtime-arm64
LABEL org.opencontainers.image.version="154.0.8037.97"

FROM runtime-${TARGETARCH}
