#!/bin/bash

################################################################################
# Container entrypoint: runs the hosts-file updater and CoreDNS side by side.
#
# CoreDNS without the updater keeps answering from a hosts file that no longer
# follows Docker, and nothing shows it. This script ends the container as soon
# as either process ends, so Docker's restart policy brings the pair back.
# Before this, the command was `update_hosts.py & exec coredns`: CoreDNS was
# PID 1 and the updater could die without anyone noticing.
#
# It also forwards SIGTERM/SIGINT, so `docker stop` stays prompt.
################################################################################

log() {
    echo "[entrypoint] $1"
}

python3 /scripts/update_hosts.py &
coredns -conf /Corefile &

stop_children() {
    # shellcheck disable=SC2046  # word splitting is wanted: one PID per job
    kill -TERM $(jobs -p) 2>/dev/null
    wait
}

trap 'log "Stop requested, shutting down"; stop_children; exit 0' TERM INT

# Returns when the first of the two exits. A trapped signal interrupts it too, but then the
# trap above runs and exits before the lines below are reached.
wait -n
status=$?

log "ERROR: the hosts updater or CoreDNS exited (status $status). Stopping the container so both are restarted together."
stop_children
exit 1
