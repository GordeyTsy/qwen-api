#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KERNEL="${KAGGLE_KERNEL:-gordeytsy/qwen-api}"
PUSH_DIR="${KAGGLE_PUSH_DIR:-${REPO_ROOT}/kaggle}"
ACCELERATOR="${KAGGLE_ACCELERATOR:-NvidiaTeslaT4}"
FORCE=0

if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
fi

status_output="$(kaggle kernels status "${KERNEL}" 2>&1 || true)"
echo "${KERNEL}: ${status_output}"
if [[ "${status_output}" == *"KernelWorkerStatus.RUNNING"* && "${FORCE}" != "1" ]]; then
  echo "refusing to push: latest ${KERNEL} is already RUNNING" >&2
  echo "stop it first, or rerun with --force only when intentionally replacing the run" >&2
  exit 3
fi

for legacy in gordeytsy/llama-cpp-ngrok gordeytsy/qwen3-6-35b-a3b-api-live-logs; do
  legacy_status="$(kaggle kernels status "${legacy}" 2>&1 || true)"
  echo "${legacy}: ${legacy_status}"
  if [[ "${legacy_status}" == *"KernelWorkerStatus.RUNNING"* && "${FORCE}" != "1" ]]; then
    echo "refusing to push: legacy notebook ${legacy} is still RUNNING" >&2
    exit 4
  fi
done

set -x
kaggle kernels push -p "${PUSH_DIR}" --accelerator "${ACCELERATOR}"

