#!/usr/bin/env bash
set -euo pipefail

base="${1:-http://127.0.0.1:8010}"

curl --fail --silent --show-error "${base}/api/v1/health/live/"
printf '\n'
curl --fail --silent --show-error "${base}/api/v1/health/ready/"
printf '\n'
