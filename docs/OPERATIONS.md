# Operations

## Quota Discipline

Only one Kaggle notebook run should be active at a time.

Before every push:

```bash
scripts/kaggle_status.sh
```

If Kaggle UI shows multiple active versions of the same notebook, stop every older version from **View Active Events**. Kaggle CLI usually reports only the latest session for a slug, so the UI is the source of truth for duplicate active versions.

Push only through the guarded helper:

```bash
scripts/push_kaggle_notebook.sh
```

The helper refuses to push when the latest `gordeytsy/qwen-api` or known legacy notebooks are already running. Use `--force` only when intentionally replacing a known bad run.

## Required Kaggle Settings

Use the CLI accelerator flag:

```bash
kaggle kernels push -p kaggle --accelerator NvidiaTeslaT4
```

Do not rely on `kernel-metadata.json` to choose T4 x2. The exact case-sensitive value `NvidiaTeslaT4` is required.

The Kaggle metadata intentionally has no `dataset_sources` and no `model_sources`; the runner downloads exactly the runtime bundle and the selected GGUF model. This avoids hidden pre-cell mounting delays and accidental downloads of unrelated models.

## Runtime Secrets

Set these through Kaggle Secrets or environment variables:

- `QWEN_API_KEY`
- `QWEN_NTFY_TOPIC`
- `QWEN_LOG_TOKEN`
- `QWEN_CONTROL_TOKEN`
- optional `QWEN_WEB_SHELL_PASSWORD`
- optional `HF_TOKEN`

If a token is absent, the runner generates an ephemeral one. Logs redact API, log, control, shell, and ntfy values.

## Release Bundle

Package a runtime bundle from a directory that contains `llama-server` and the required `.so` files:

```bash
scripts/package_runtime_bundle.sh /home/gt/private-ai-agents/kaggle/dataset_public
```

Create or update the GitHub Release asset:

```bash
gh release create runtime-bundle-sm75-v1 dist/llama-server-cuda-sm75-linux-x86_64.tar.gz \
  --title runtime-bundle-sm75-v1 \
  --notes "CUDA sm75 llama-server runtime"
```

For updates to an existing release:

```bash
gh release upload runtime-bundle-sm75-v1 dist/llama-server-cuda-sm75-linux-x86_64.tar.gz --clobber
```

## Logs

Expected early output:

```text
BOOT: qwen-api bootstrap started
BOOT: qwen-api runner started
```

Important events:

- `gpu_checked`: confirms T4 x2 and total VRAM.
- `observability_ready`: `log_url` and `control_url` are available.
- `runtime_ready`: release bundle extracted and `llama-server --version` ran.
- `downloading_model`: GGUF download started.
- `ready`: `base_url` is ready for OpenAI-compatible requests.
- `ready_keepalive`: remote process is alive.

Use the log tunnel:

```bash
curl "$LOG_URL/files?token=$QWEN_LOG_TOKEN"
curl "$LOG_URL/events?token=$QWEN_LOG_TOKEN"
```

## Stop A Run

Preferred remote stop:

```bash
curl -X POST "$CONTROL_URL/stop?token=$QWEN_CONTROL_TOKEN"
```

Fallback: stop from Kaggle UI **View Active Events**. Use UI when there are duplicate active versions of the same notebook, because the public CLI status endpoint reports only the latest session.

## Smoke Test

After `ready`, run:

```bash
curl "$BASE_URL/models" \
  -H "Authorization: Bearer $QWEN_API_KEY"
```

Then:

```bash
curl "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $QWEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6-35b-a3b-hauhaucs-aggressive","messages":[{"role":"user","content":"Reply with OK only."}],"max_tokens":8}'
```

