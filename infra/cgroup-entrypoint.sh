#!/bin/bash
set -eu
# All changes stay inside this container's private cgroup namespace.
if [ ! -f /sys/fs/cgroup/cgroup.controllers ]; then
  echo 'This image requires cgroup v2.' >&2
  exit 1
fi
mkdir -p /sys/fs/cgroup/manager /sys/fs/cgroup/boxes
while read -r task_pid; do
  echo "$task_pid" > /sys/fs/cgroup/manager/cgroup.procs 2>/dev/null || true
done < /sys/fs/cgroup/cgroup.procs
echo '+cpu +memory +pids' > /sys/fs/cgroup/cgroup.subtree_control
echo '+cpu +memory +pids' > /sys/fs/cgroup/boxes/cgroup.subtree_control
mkdir -p /run/isolate/locks
exec runuser -u judge0 -- /api/docker-entrypoint.sh "$@"
