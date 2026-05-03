# qwen-api

Kaggle runtime for an OpenAI-compatible Qwen API backed by `llama-server` and exposed through Cloudflare Quick Tunnels.

The Kaggle notebook is intentionally tiny: it clones this public repo, installs it, and runs `python -m qwen_api.kaggle_runner`. All operational logic, logging, release tooling, and documentation live here.

## Current Defaults

- Model: `HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive`
- First quant: `Q4_K_P`
- Fallback quant: `Q4_K_M`
- Context attempts: `131072`, then `102400`
- Accelerator: Kaggle `NvidiaTeslaT4` via CLI flag
- Tunnel: Cloudflare Quick Tunnel
- Runtime bundle: GitHub Release asset, not git

## Quick Start

1. Create or update the runtime bundle release:

   ```bash
   scripts/package_runtime_bundle.sh /path/to/llama-server-bundle
   gh release create runtime-bundle-sm75-v1 dist/llama-server-cuda-sm75-linux-x86_64.tar.gz --title runtime-bundle-sm75-v1 --notes "CUDA sm75 llama-server runtime"
   ```

2. Push the Kaggle notebook:

   ```bash
   scripts/push_kaggle_notebook.sh
   ```

3. Watch logs:

   ```bash
   scripts/kaggle_status.sh
   kaggle kernels logs -f --interval 10 gordeytsy/qwen-api
   ```

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for run control, stopping stale notebooks, and troubleshooting.

