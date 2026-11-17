#!/usr/bin/env bash
# Prepare a reference scoring machine (Ubuntu, Intel c4-standard-32, one thread per core).
# Run once as root on a fresh machine, before any scoring session. Idempotent.
#
# - installs Docker CE, Git LFS and the host tools the judge checks for;
# - disables and masks background timers (automatic updates, snap, man-db, ...);
# - reserves CPUs 8 and up for the system: systemd's manager, Docker and containerd
#   run there through CPUAffinity, leaving CPUs 0-7 to the judge and its containers;
# - lowers Docker bridge MTUs to 1400 and clamps TCP MSS (tools.scoring_host fix-network),
#   which cloud networks such as Google Cloud's (MTU 1460) need; rerun that command after
#   every reboot or Docker restart;
# - writes a host description to /opt/optimbench-receipts/base-host.txt.
#
# The judge records the CPUs system services can run on and its own CPUs; a score is
# claimable only when they are 8-15 and 0-7. Run the judge itself under taskset -c 0-7.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# Cloud package mirrors are occasionally mid-sync and return 404 for a file the index
# names; refresh the index and retry rather than failing the whole provisioning.
apt_install() {
  local attempt
  for attempt in 1 2 3 4; do
    apt-get update && apt-get install -y "$@" && return 0
    echo "apt-get install failed (attempt $attempt); retrying in 30s" >&2
    apt-get clean; sleep 30
  done
  return 1
}
# ffmpeg is not installed: no v1.0 Task needs it (the judge requires it only for
# png-rgb24 public inputs, and checks for it when a Task does).
apt_install ca-certificates curl gnupg git git-lfs binutils python3-venv build-essential iproute2 iptables jq linux-tools-common
install -m 0755 -d /etc/apt/keyrings
if [ ! -s /etc/apt/keyrings/docker.asc ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
for unit in unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service snapd.service snapd.socket snapd.seeded.service snapd.snap-repair.timer fwupd-refresh.timer motd-news.timer man-db.timer plocate-updatedb.timer; do
  if systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q "^$unit"; then
    systemctl disable --now "$unit" || true
    systemctl mask "$unit"
  fi
done
install -d /etc/apt/apt.conf.d
printf 'APT::Periodic::Enable "0";\nAPT::Periodic::Update-Package-Lists "0";\nAPT::Periodic::Unattended-Upgrade "0";\n' > /etc/apt/apt.conf.d/99-optimbench-no-automatic-updates
if [ "$(uname -m)" = x86_64 ]; then
  last=$(($(nproc --all)-1))
  systemcpus="8-$last"
  for svc in docker containerd; do
    install -d "/etc/systemd/system/$svc.service.d"
    printf '[Service]\nCPUAffinity=%s\n' "$systemcpus" > "/etc/systemd/system/$svc.service.d/optimbench-cpu-affinity.conf"
  done
  install -d /etc/systemd/system.conf.d
  printf '[Manager]\nCPUAffinity=%s\n' "$systemcpus" > /etc/systemd/system.conf.d/optimbench-cpu-affinity.conf
  systemctl daemon-reload
  systemctl restart containerd docker
  # CPUAffinity, not cgroup AllowedCPUs: explicit judge --cpuset-cpus=0-7
  # must still be able to use the reserved CPUs.
  while read -r pid; do
    [ "$pid" -eq 1 ] && continue
    [ -r "/proc/$pid/exe" ] || continue
    taskset -apc "$systemcpus" "$pid" >/dev/null 2>&1 || true
  done < <(ps -e -o pid=)
fi
install -d /opt/optimbench-receipts
{
  date -u --iso-8601=seconds
  uname -a
  lscpu
  lscpu -e=CPU,NODE,SOCKET,CORE,ONLINE
  grep -m1 '^microcode' /proc/cpuinfo || true
  cat /sys/devices/system/cpu/online
  stat -fc %T /sys/fs/cgroup
  cat /sys/fs/cgroup/cgroup.controllers
  for f in /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/intel_pstate/status /sys/devices/system/cpu/cpufreq/boost; do
    if [ -r "$f" ]; then printf '%s: ' "$f"; cat "$f"; else printf '%s: unavailable\n' "$f"; fi
  done
  docker version
  docker buildx version
  docker info --format 'cgroup={{.CgroupVersion}} driver={{.CgroupDriver}} CPUs={{.NCPU}}'
  for svc in docker containerd; do systemctl show "$svc" -p CPUAffinity -p MainPID; done
  systemctl list-timers --all --no-pager
} > /opt/optimbench-receipts/base-host.txt
# Docker's bridges default to MTU 1500, larger than cloud networks allow; the judge's setup
# refuses to run until they are lowered and TCP MSS is clamped. Docker was restarted above,
# so apply the repair now. It is lost on reboot or Docker restart: rerun it then.
(cd "$(dirname "$(readlink -f "$0")")/.." && python3 -m tools.scoring_host fix-network --mtu 1400 --confirm-no-timing-session)
echo "Base provisioning complete; host receipt: /opt/optimbench-receipts/base-host.txt"
