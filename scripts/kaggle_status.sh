#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -gt 0 ]]; then
  kernels=("$@")
else
  kernels=(gordeytsy/qwen-api gordeytsy/llama-cpp-ngrok gordeytsy/qwen3-6-35b-a3b-api-live-logs)
fi

for kernel in "${kernels[@]}"; do
  echo "== ${kernel} =="
  kaggle kernels status "${kernel}" 2>&1 || true
done

echo "== recent notebooks =="
kaggle kernels list -m -s "${KAGGLE_USERNAME:-gordeytsy}" 2>&1 | sed -n '1,40p'
