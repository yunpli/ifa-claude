#!/usr/bin/env bash
set -euo pipefail

# Third-party scheduler entry for the Beijing 22:40 production incremental.
# It intentionally delegates to the canonical incremental script so operational
# flags stay in one place.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

exec "${ROOT_DIR}/scripts/sme_incremental_0300.sh"
