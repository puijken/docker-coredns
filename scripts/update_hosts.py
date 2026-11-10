#!/usr/bin/env python3
import os
import threading
import time

import docker

# -------------------------
# Configuration
# -------------------------
DOMAIN = os.environ.get("DOMAIN", "docker.local")
HOSTS_FILE = "/etc/coredns/hosts"
NETWORK_PREFIXES = os.environ.get("NETWORK_PREFIX", "macvlan").split(",")
FALLBACK_IP = os.environ.get("FALLBACK_IP", "0.0.0.0")
# How long to wait after an event before rewriting the hosts file, so that a burst of events
# (a host booting, a compose stack coming up) costs one rewrite instead of one per event.
DEBOUNCE_SECONDS = float(os.environ.get("DEBOUNCE_SECONDS", "1"))
# Pause before retrying a failed rewrite or re-subscribing to a dropped event stream.
RETRY_SECONDS = 5

# Docker client
client = docker.from_env()

# -------------------------
# Address selection
# -------------------------
def resolve_ip(net_info):
    """The address to publish for one container-on-network attachment.

    "IPAddress" is the *runtime* address: Docker populates it while the container runs and clears
    it as soon as it stops. Taking it alone meant every stopped container fell through to
    FALLBACK_IP and published a 0.0.0.0 record, even though a statically assigned container knows
    its address perfectly well -- Docker keeps it under IPAMConfig.IPv4Address whatever the
    container's state. That mattered most for exactly the containers meant to sit stopped: the
    on-demand ones started by a docker-watchdog only on request, whose DNS has to work *before*
    anything starts them.

    FALLBACK_IP still applies to an attachment with no static address configured, which is the
    case it was there for.
    """
    runtime = net_info.get("IPAddress")
    if runtime:
        return runtime

    ipam = net_info.get("IPAMConfig") or {}
    configured = ipam.get("IPv4Address")
    if configured:
        return configured

    return FALLBACK_IP


# -------------------------
# Generate hosts file
# -------------------------
def generate_hosts():
    # Include all containers, even stopped ones. ignore_removed: a container destroyed between the
    # list and its inspect is skipped instead of failing the whole update with "No such container".
    containers = client.containers.list(all=True, ignore_removed=True)
    lines = []

    for c in containers:
        name = c.name
        networks = c.attrs["NetworkSettings"]["Networks"]

        # Only add an entry for networks matching one of the configured prefixes
        for net_name, net_info in networks.items():
            if not any(net_name.startswith(prefix.strip()) for prefix in NETWORK_PREFIXES):
                continue

            ip = resolve_ip(net_info)
            fqdn = f"{name}.{DOMAIN}"
            lines.append(f"{ip}\t{fqdn} {name}")

    write_hosts("\n".join(lines) + "\n")

    print(f"✅ Updated {HOSTS_FILE} with {len(lines)} entries "
          f"for networks: {', '.join(NETWORK_PREFIXES)}")



def write_hosts(content):
    """Replaces the hosts file in one step.

    CoreDNS re-reads the file every 2 seconds. Opening it for writing truncates it first, so a
    reload landing between the truncate and the write served an empty or half-written file, and
    every name in it failed to resolve until the next reload. Writing a sibling file and renaming
    it over the old one means a reader sees the old content or the new, never a partial file.
    """
    tmp = f"{HOSTS_FILE}.tmp"
    with open(tmp, "w") as f:
        f.write(content)
    try:
        os.replace(tmp, HOSTS_FILE)
    except OSError:
        # The hosts file itself is a mount point (a single-file bind mount), which cannot be
        # renamed over. Fall back to writing in place rather than not updating at all.
        with open(HOSTS_FILE, "w") as f:
            f.write(content)
        os.unlink(tmp)


# -------------------------
# Event listener
# -------------------------
# Events that can change an address. Matched exactly: a substring match also caught
# "exec_start: <cmd>" and "exec_die", so every healthcheck rewrote the hosts file (and logged the
# command line). Network attach/detach arrive as Type "network", not "container".
RELEVANT_EVENTS = {
    "container": {"start", "die", "destroy"},
    "network": {"connect", "disconnect"},
}


# Set by anything that wants the hosts file rewritten; cleared by run_pending_update().
_update_requested = threading.Event()


def handle_events(events):
    """Requests a rewrite for every relevant event in `events`; returns when the stream ends.

    It only raises the flag. The rewrite itself happens in run_pending_update(), once per burst:
    each one lists and inspects every container, and a host starting 40 containers used to do
    that 40 times over, in the middle of the busiest minute the Docker daemon has.
    """
    for event in events:
        kind = event.get("Type", "")
        action = event.get("Action", "")
        if action in RELEVANT_EVENTS.get(kind, ()):
            # One line per burst, not per event: the first one that raises the flag.
            if not _update_requested.is_set():
                print(f"🔁 Docker event: {kind} {action} - hosts file update requested")
            _update_requested.set()


def run_pending_update():
    """Waits for a requested rewrite, lets the rest of its burst arrive, then rewrites once.

    The flag is cleared *before* generating, so an event that lands during the rewrite is not
    lost: it raises the flag again and the next call picks it up. A failed rewrite is retried
    after RETRY_SECONDS instead of waiting for whatever event happens to come next.
    """
    _update_requested.wait()
    time.sleep(DEBOUNCE_SECONDS)
    _update_requested.clear()
    try:
        generate_hosts()
    except Exception as e:
        print(f"❌ Error updating hosts: {e} (retrying in {RETRY_SECONDS}s)")
        time.sleep(RETRY_SECONDS)
        _update_requested.set()


def update_worker():
    while True:
        run_pending_update()


def listen_forever():
    """Subscribes to Docker events and re-subscribes whenever the stream ends or fails.

    The stream used to be read once: when it broke, this script exited, CoreDNS carried on
    serving the last hosts file, and nothing said the records had stopped following Docker.
    Every (re)subscription also requests a full rewrite, which picks up anything that changed
    while nobody was listening. Subscribe first, then request, so nothing falls in between.
    """
    while True:
        try:
            events = client.events(decode=True, filters={"type": list(RELEVANT_EVENTS)})
            _update_requested.set()
            handle_events(events)
            print(f"⚠️ Docker event stream ended; re-subscribing in {RETRY_SECONDS}s")
        except Exception as e:
            print(f"❌ Docker event stream failed: {e}; re-subscribing in {RETRY_SECONDS}s")
        time.sleep(RETRY_SECONDS)


# -------------------------
# Main
# -------------------------
if __name__ == "__main__":
    # Initial population, straight away rather than after the debounce delay. Retried: at boot
    # the Docker socket can be a moment behind this container.
    while True:
        try:
            generate_hosts()
            break
        except Exception as e:
            print(f"❌ Error updating hosts: {e} (retrying in {RETRY_SECONDS}s)")
            time.sleep(RETRY_SECONDS)

    threading.Thread(target=update_worker, daemon=True).start()
    print(f"📡 Listening for Docker events "
          f"with domain '{DOMAIN}' and network prefixes '{', '.join(NETWORK_PREFIXES)}' ...")
    listen_forever()