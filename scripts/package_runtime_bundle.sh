#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="${1:-}"
OUT_DIR="${2:-dist}"
ASSET_NAME="${QWEN_RUNTIME_ASSET:-llama-server-cuda-sm75-linux-x86_64.tar.gz}"

if [[ -z "${SRC_DIR}" || ! -d "${SRC_DIR}" ]]; then
  echo "usage: $0 /path/to/runtime-dir [out-dir]" >&2
  exit 2
fi

if [[ ! -f "${SRC_DIR}/llama-server" ]]; then
  echo "missing ${SRC_DIR}/llama-server" >&2
  exit 2
fi

rm -rf runtime-bundle "${OUT_DIR}"
mkdir -p runtime-bundle "${OUT_DIR}"

cp -av "${SRC_DIR}/llama-server" runtime-bundle/
find "${SRC_DIR}" -maxdepth 1 -type f -name '*.so*' -exec cp -av {} runtime-bundle/ \;
chmod 0755 runtime-bundle/llama-server

(
  cd runtime-bundle
  {
    echo "{"
    echo '  "name": "llama-server-cuda-sm75-linux-x86_64",'
    echo '  "cuda_arch": "sm75",'
    echo '  "created_by": "scripts/package_runtime_bundle.sh",'
    echo '  "files": ['
    find . -maxdepth 1 -type f ! -name manifest.json ! -name SHA256SUMS -printf '%f\n' | sort | sed 's/.*/    "&",/' | sed '$ s/,$//'
    echo '  ]'
    echo "}"
  } > manifest.json
  sha256sum * > SHA256SUMS
)

tar -C runtime-bundle -czf "${OUT_DIR}/${ASSET_NAME}" .
sha256sum "${OUT_DIR}/${ASSET_NAME}" > "${OUT_DIR}/${ASSET_NAME}.sha256"
echo "wrote ${OUT_DIR}/${ASSET_NAME}"

