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

RUN mkdir -p /etc/coredns && chmod +x /scripts/update_hosts.py
RUN pip install docker

EXPOSE 53/udp
EXPOSE 53/tcp

CMD ["/bin/sh", "-c", "python3 /scripts/update_hosts.py & exec coredns -conf /Corefile"]