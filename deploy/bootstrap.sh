#!/usr/bin/env bash
# Provision a clean Amazon Linux 2023 arm64 instance to run the recorder.
# Run as root on the instance. Idempotent.
#
# Configuration comes from the environment (user-data exports it), NOT from positional args:
#   FUNDR_BUCKET   required, the S3 bucket
#   FUNDR_GIT_SHA  required, the commit to deploy
#   FUNDR_REPO     required, git URL (ssh form when using a deploy key)
#   FUNDR_INSTANCE_ID  optional, defaults to the EC2 instance id
set -euo pipefail

: "${FUNDR_BUCKET:?FUNDR_BUCKET must be set in the environment}"
: "${FUNDR_GIT_SHA:?FUNDR_GIT_SHA must be set in the environment}"
: "${FUNDR_REPO:?FUNDR_REPO must be set in the environment}"

TOKEN="$(curl -sX PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
INSTANCE_ID="${FUNDR_INSTANCE_ID:-$(curl -s -H "X-aws-ec2-metadata-token: ${TOKEN}" \
  http://169.254.169.254/latest/meta-data/instance-id)}"

dnf -y update
dnf -y install git chrony
systemctl enable --now chronyd            # requirement 4: trustworthy wall clock

id fundr &>/dev/null || useradd --system --home /opt/fundr --shell /usr/sbin/nologin fundr
install -d -o fundr -g fundr /opt/fundr /var/lib/fundr /etc/fundr /opt/fundr/.cache

curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

cat >/etc/fundr/recorder.env <<ENV
FUNDR_BUCKET=${FUNDR_BUCKET}
FUNDR_GIT_SHA=${FUNDR_GIT_SHA}
FUNDR_INSTANCE_ID=${INSTANCE_ID}
ENV
chown fundr:fundr /etc/fundr/recorder.env
chmod 640 /etc/fundr/recorder.env

# Read-only deploy key, written by user-data (Task 10). Absent for a public repo.
if [[ -f /etc/fundr/deploy_key ]]; then
  chown fundr:fundr /etc/fundr/deploy_key
  chmod 600 /etc/fundr/deploy_key
  export GIT_SSH_COMMAND="ssh -i /etc/fundr/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

# -H is required: without it sudo keeps HOME=/root, and uv writes its cache and venv metadata
# into a directory the fundr user cannot read, which fails on AL2023. UV_CACHE_DIR pins the
# cache somewhere the service account owns.
run_as_fundr() {
  sudo -u fundr -H -E env "UV_CACHE_DIR=/opt/fundr/.cache" "GIT_SSH_COMMAND=${GIT_SSH_COMMAND:-ssh}" "$@"
}

run_as_fundr git -C /opt/fundr rev-parse --git-dir &>/dev/null || \
  run_as_fundr git clone "${FUNDR_REPO}" /opt/fundr
run_as_fundr git -C /opt/fundr fetch --all
run_as_fundr git -C /opt/fundr checkout "${FUNDR_GIT_SHA}"
run_as_fundr /usr/local/bin/uv --directory /opt/fundr sync --no-dev

install -m 644 /opt/fundr/deploy/fundr-recorder.service /etc/systemd/system/
install -m 644 /opt/fundr/deploy/fundr-upload.service /etc/systemd/system/
install -m 644 /opt/fundr/deploy/fundr-upload.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/fundr-recorder.service \
                       /etc/systemd/system/fundr-upload.service \
                       /etc/systemd/system/fundr-upload.timer
systemctl daemon-reload
systemctl enable --now fundr-recorder.service
systemctl enable --now fundr-upload.timer
systemctl --no-pager status fundr-recorder.service
