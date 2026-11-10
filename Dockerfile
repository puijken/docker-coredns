# Stage 1: Build CoreDNS
FROM coredns/coredns:latest as coredns

# Stage 2: Build final image
FROM python:3.15-slim

LABEL maintainer="puijken"
LABEL description="CoreDNS with dynamic Docker discovery (A + PTR records)"
LABEL org.opencontainers.image.source="https://github.com/puijken/docker-coredns"

COPY --from=coredns /coredns /usr/local/bin/coredns
COPY Corefile /Corefile
COPY scripts/update_hosts.py /scripts/update_hosts.py
COPY entrypoint.sh /entrypoint.sh

RUN mkdir -p /etc/coredns && chmod +x /scripts/update_hosts.py /entrypoint.sh
RUN pip install docker

# Without this Python block-buffers stdout when it is not a terminal, and the updater's messages
# (errors included) sat in a buffer instead of reaching `docker logs`.
ENV PYTHONUNBUFFERED=1

EXPOSE 53/udp
EXPOSE 53/tcp

# Runs the updater and CoreDNS, ends the container when either exits, forwards SIGTERM.
CMD ["/entrypoint.sh"]