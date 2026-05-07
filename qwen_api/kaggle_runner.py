import hashlib
import json
import os
import pathlib
import re
import secrets
import signal
import subprocess
import tarfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

print("BOOT: qwen-api runner started", flush=True)

OWNER_REPO = "GordeyTsy/qwen-api"
RUNTIME_TAG = os.getenv("QWEN_RUNTIME_TAG", "runtime-bundle-sm75-v1")
RUNTIME_ASSET = os.getenv("QWEN_RUNTIME_ASSET", "llama-server-cuda-sm75-linux-x86_64.tar.gz")
RUNTIME_URL = os.getenv(
    "QWEN_RUNTIME_URL",
    f"https://github.com/{OWNER_REPO}/releases/download/{RUNTIME_TAG}/{RUNTIME_ASSET}",
)
MODEL_REPO = os.getenv("QWEN_MODEL_REPO", "HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive")
MODEL_ALIAS = os.getenv("QWEN_MODEL_ALIAS", "qwen3.6-35b-a3b-hauhaucs-aggressive")
MODEL_FILES = os.getenv(
    "QWEN_MODEL_FILES",
    "Q4_K_P:Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf,"
    "Q4_K_M:Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf",
)
CTX_SIZES = [int(item) for item in os.getenv("QWEN_CTX_SIZES", "262144,196608,131072,102400").split(",") if item.strip()]

PORT = int(os.getenv("QWEN_API_PORT", "8000"))
API_KEY = os.getenv("QWEN_API_KEY") or "sk-kaggle-" + secrets.token_urlsafe(24)
NTFY_TOPIC = os.getenv("QWEN_NTFY_TOPIC", "")
LOG_TOKEN = os.getenv("QWEN_LOG_TOKEN") or "log-" + secrets.token_urlsafe(32)
CONTROL_TOKEN = os.getenv("QWEN_CONTROL_TOKEN") or "ctrl-" + secrets.token_urlsafe(32)
OBS_INTERVAL = int(os.getenv("QWEN_OBS_INTERVAL", "10"))
IDLE_TIMEOUT = int(os.getenv("QWEN_IDLE_TIMEOUT_SECONDS", "300"))
ENABLE_SHELL = os.getenv("QWEN_ENABLE_WEB_SHELL", "0").lower() in {"1", "true", "yes", "on"}
SHELL_PASSWORD = os.getenv("QWEN_WEB_SHELL_PASSWORD") or "pw-" + secrets.token_urlsafe(24)

WORK = pathlib.Path(os.getenv("QWEN_WORK_DIR", "/kaggle/working"))
TMP = pathlib.Path(os.getenv("QWEN_TEMP_DIR", "/kaggle/temp/qwen_api"))
BIN = TMP / "bin"
LOGS = WORK / "logs"
RUNTIME = TMP / "runtime"
MODELS = TMP / "models"
for directory in (WORK, TMP, BIN, LOGS, RUNTIME, MODELS):
    directory.mkdir(parents=True, exist_ok=True)

CLOUDFLARED = BIN / "cloudflared"
TTYD = BIN / "ttyd"
SERVER = RUNTIME / "llama-server"
STATE = WORK / "api_connection.json"
EVENTS = LOGS / "events.jsonl"
PIDS = [TMP / name for name in ("llama.pid", "api-tunnel.pid", "log-tunnel.pid", "control-tunnel.pid", "shell.pid", "shell-tunnel.pid")]
LAST_TOUCH = time.monotonic()
READY = threading.Event()
STOP = threading.Event()
CONTROL_URL = ""


def now():
    return datetime.now(timezone.utc).isoformat()


def redact(value):
    text = str(value)
    for secret in (API_KEY, LOG_TOKEN, CONTROL_TOKEN, SHELL_PASSWORD, NTFY_TOPIC):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"sk-kaggle-[A-Za-z0-9_-]+", "sk-kaggle-[REDACTED]", text)
    text = re.sub(r"log-[A-Za-z0-9_-]+", "log-[REDACTED]", text)
    text = re.sub(r"ctrl-[A-Za-z0-9_-]+", "ctrl-[REDACTED]", text)
    text = re.sub(r"(token=)[^&\s]+", r"\1[REDACTED]", text)
    return text


def event(kind, **payload):
    payload.update({"kind": kind, "updated_at": now()})
    safe = json.loads(redact(json.dumps(payload, sort_keys=True)))
    line = json.dumps(safe, sort_keys=True)
    with EVENTS.open("a", encoding="utf-8") as file:
        file.write(line + "\n")
    print(json.dumps(safe, indent=2, sort_keys=True), flush=True)
    if NTFY_TOPIC:
        try:
            public = dict(safe)
            public.pop("api_key", None)
            data = json.dumps(public, sort_keys=True).encode("utf-8")
            req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Title", "kaggle-qwen-api")
            urllib.request.urlopen(req, timeout=8).read()
        except Exception as exc:
            print(f"ntfy failed: {redact(exc)}", flush=True)


def state(**updates):
    current = {}
    if STATE.exists():
        try:
            current = json.loads(STATE.read_text())
        except Exception:
            current = {}
    current.update(updates)
    current.setdefault("model", MODEL_ALIAS)
    current.setdefault("api_key", API_KEY)
    current["updated_at"] = now()
    STATE.write_text(redact(json.dumps(current, indent=2, sort_keys=True)))
    print(redact(json.dumps(current, indent=2, sort_keys=True)), flush=True)
    return current


def tail(path, size=12000):
    path = pathlib.Path(path)
    if not path.exists():
        return ""
    return path.read_bytes()[-size:].decode("utf-8", "replace")


def run_logged(stage, cmd, log_name, check=True, cwd=None, progress=None):
    log_path = LOGS / log_name
    redacted_cmd = [redact(part) for part in cmd]
    event("stage_start", stage=stage, cmd=redacted_cmd, log_path=str(log_path))
    with log_path.open("ab") as file:
        file.write(("\n# " + now() + " stage=" + stage + "\n").encode())
        file.write(("$ " + " ".join(redacted_cmd) + "\n").encode())
        file.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=file, stderr=subprocess.STDOUT, start_new_session=True)
        start = time.time()
        last = 0.0
        while proc.poll() is None:
            if time.time() - last >= OBS_INTERVAL:
                event(
                    "stage_progress",
                    stage=stage,
                    pid=proc.pid,
                    elapsed_seconds=int(time.time() - start),
                    log_tail=redact(tail(log_path, 6000)),
                    **(progress() if progress else {}),
                )
                last = time.time()
            time.sleep(2)
    event("stage_end", stage=stage, returncode=proc.returncode, log_tail=redact(tail(log_path, 6000)))
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc.returncode


def download(url, dst, min_size=1):
    dst = pathlib.Path(dst)
    if dst.exists() and dst.stat().st_size >= min_size:
        event("download_cached", path=str(dst), bytes=dst.stat().st_size)
        return

    def progress():
        return {"path": str(dst), "bytes": dst.stat().st_size if dst.exists() else 0}

    run_logged(
        "download_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", dst.name),
        ["curl", "-L", "--fail", "--retry", "8", "--retry-delay", "4", "-C", "-", "-o", str(dst), url],
        "download-" + dst.name + ".log",
        progress=progress,
    )


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest():
    sums = RUNTIME / "SHA256SUMS"
    if not sums.exists():
        raise RuntimeError("runtime bundle is missing SHA256SUMS")
    for raw in sums.read_text().splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        expected, name = raw.split(None, 1)
        name = name.lstrip("*")
        path = RUNTIME / name
        if not path.exists():
            raise RuntimeError(f"runtime bundle is missing {name}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"checksum mismatch for {name}: {actual} != {expected}")


def ensure_runtime_bundle():
    if SERVER.exists():
        return
    archive = TMP / RUNTIME_ASSET
    state(status="downloading_runtime_bundle", runtime_url=RUNTIME_URL)
    download(RUNTIME_URL, archive, 1_000_000)
    event("extract_runtime_bundle", archive=str(archive), target=str(RUNTIME))
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(RUNTIME)
    verify_manifest()
    SERVER.chmod(0o755)
    os.environ["LD_LIBRARY_PATH"] = str(RUNTIME) + ":/usr/local/cuda/lib64:" + os.environ.get("LD_LIBRARY_PATH", "")
    run_logged("llama_server_version", [str(SERVER), "--version"], "llama-server-version.log", check=False)
    state(status="runtime_ready", binary=str(SERVER))


def gpu_check():
    output = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
    ).decode()
    gpus = []
    for line in output.strip().splitlines():
        name, memory = line.rsplit(",", 1)
        gpus.append({"name": name.strip(), "memory_mib": int(memory.strip())})
    total = sum(gpu["memory_mib"] for gpu in gpus)
    state(status="gpu_checked", gpus=gpus, total_vram_mib=total)
    if len(gpus) < 2 or total < 28000:
        raise RuntimeError(f"Need T4 x2 / about 30 GiB VRAM, got {len(gpus)} GPU(s), total {total} MiB")


def parse_model_files():
    parsed = []
    for item in MODEL_FILES.split(","):
        quant, filename = item.split(":", 1)
        parsed.append((quant.strip(), filename.strip()))
    return parsed


def ensure_cloudflared():
    download("https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64", CLOUDFLARED, 1_000_000)
    CLOUDFLARED.chmod(0o755)


def tunnel(port, pidfile, log_name):
    log = LOGS / log_name
    with log.open("ab") as file:
        proc = subprocess.Popen(
            [str(CLOUDFLARED), "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pathlib.Path(pidfile).write_text(str(proc.pid))
    pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    for _ in range(120):
        if proc.poll() is not None:
            raise RuntimeError(tail(log))
        match = pattern.search(tail(log, 30000))
        if match:
            return match.group(0)
        time.sleep(1)
    raise RuntimeError("no cloudflare url: " + tail(log))


def log_files():
    return [
        {"name": str(path.relative_to(LOGS)), "bytes": path.stat().st_size}
        for path in sorted(LOGS.rglob("*"))
        if path.is_file()
    ]


class LogHandler(BaseHTTPRequestHandler):
    def authorized(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return (self.headers.get("X-Log-Token") or (query.get("token") or [""])[0]) == LOG_TOKEN

    def do_GET(self):
        if not self.authorized():
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"forbidden")
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/files":
            body = json.dumps(log_files(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        name = "events.jsonl" if parsed.path == "/events" else urllib.parse.unquote(parsed.path.removeprefix("/log/")).lstrip("/")
        path = (LOGS / name).resolve()
        if LOGS.resolve() not in path.parents and path != LOGS.resolve():
            self.send_response(400)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        offset = 0
        query = urllib.parse.parse_qs(parsed.query)
        if "offset" in query:
            offset = int(query["offset"][0] or "0")
        while True:
            try:
                if path.exists():
                    with path.open("rb") as file:
                        file.seek(offset)
                        chunk = file.read()
                        if chunk:
                            offset += len(chunk)
                            self.wfile.write(redact(chunk.decode("utf-8", "replace")).encode())
                            self.wfile.flush()
                time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                return

    def log_message(self, fmt, *args):
        return


class ControlHandler(BaseHTTPRequestHandler):
    def authorized(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return (self.headers.get("X-Control-Token") or (query.get("token") or [""])[0]) == CONTROL_TOKEN

    def send_json(self, code, payload):
        body = redact(json.dumps(payload, indent=2, sort_keys=True)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authorized():
            self.send_json(403, {"error": "forbidden"})
            return
        self.send_json(200, {"status": "ready" if READY.is_set() else "starting", "idle_seconds": int(time.monotonic() - LAST_TOUCH)})

    def do_POST(self):
        global LAST_TOUCH
        if not self.authorized():
            self.send_json(403, {"error": "forbidden"})
            return
        route = urllib.parse.urlsplit(self.path).path
        if route == "/touch":
            LAST_TOUCH = time.monotonic()
            self.send_json(200, {"ok": True})
            return
        if route == "/stop":
            self.send_json(200, {"ok": True, "status": "stopping"})
            threading.Thread(target=request_shutdown, args=("remote_stop",), daemon=True).start()
            return
        self.send_json(404, {"error": "not_found"})

    def log_message(self, fmt, *args):
        return


def start_http_server(port, handler):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def kill_pidfile(path):
    path = pathlib.Path(path)
    if not path.exists():
        return
    try:
        pid = int(path.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        time.sleep(2)
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
    path.unlink(missing_ok=True)


def stop_processes():
    for pidfile in PIDS:
        kill_pidfile(pidfile)


def request_shutdown(reason):
    if STOP.is_set():
        return
    STOP.set()
    event("shutdown_requested", reason=reason)
    stop_processes()
    state(status="stopped", stopped_at=now())
    time.sleep(0.5)
    os._exit(0)


def wait_server(proc, log):
    for _ in range(180):
        if proc.poll() is not None:
            raise RuntimeError(tail(log))
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/models", headers={"Authorization": "Bearer " + API_KEY})
            urllib.request.urlopen(req, timeout=5).read()
            return
        except Exception:
            event("server_wait", log_tail=redact(tail(log, 3000)))
            time.sleep(5)
    raise RuntimeError("server not ready: " + tail(log))


def start_model(quant, filename, ctx):
    global LAST_TOUCH
    for old in MODELS.glob("*.gguf"):
        if old.name != filename:
            old.unlink(missing_ok=True)
    model_path = MODELS / filename
    model_url = f"https://huggingface.co/{MODEL_REPO}/resolve/main/{filename}?download=true"
    if os.getenv("HF_TOKEN"):
        model_url = model_url + "&token=" + urllib.parse.quote(os.getenv("HF_TOKEN", ""))
    state(status="downloading_model", quant=quant, model_file=filename)
    download(model_url, model_path, 1_000_000_000)
    log = LOGS / f"llama-{quant}-{ctx}.log"
    cmd = [
        str(SERVER),
        "-m", str(model_path),
        "-a", MODEL_ALIAS,
        "--host", "127.0.0.1",
        "--port", str(PORT),
        "--api-key", API_KEY,
        "--jinja",
        "-c", str(ctx),
        "-ngl", "999",
        "-sm", "layer",
        "-ts", "1,1",
        "-fa", "on",
        "-ctk", os.getenv("QWEN_CACHE_TYPE_K", "q4_0"),
        "-ctv", os.getenv("QWEN_CACHE_TYPE_V", "q4_0"),
        "-np", "1",
        "-b", os.getenv("QWEN_BATCH_SIZE", "512"),
        "-ub", os.getenv("QWEN_UBATCH_SIZE", "128"),
        "--chat-template-kwargs", '{"enable_thinking":false}',
    ]
    if os.getenv("QWEN_NO_KV_OFFLOAD", "0").lower() in {"1", "true", "yes", "on"}:
        cmd.append("--no-kv-offload")
    with log.open("ab") as file:
        file.write(("$ " + " ".join(redact(part) for part in cmd) + "\n").encode())
        proc = subprocess.Popen(cmd, stdout=file, stderr=subprocess.STDOUT, start_new_session=True)
    (TMP / "llama.pid").write_text(str(proc.pid))
    wait_server(proc, log)
    LAST_TOUCH = time.monotonic()
    READY.set()
    state(status="ready", quant=quant, context_size=ctx, server_pid=proc.pid, server_log=str(log))
    return proc


def start_optional_shell():
    if not ENABLE_SHELL:
        return None
    download("https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.x86_64", TTYD, 1_000_000)
    TTYD.chmod(0o755)
    log = LOGS / "shell.log"
    with log.open("ab") as file:
        proc = subprocess.Popen([str(TTYD), "-i", "127.0.0.1", "-p", "8022", "-c", f"kaggle:{SHELL_PASSWORD}", "bash", "-l"], stdout=file, stderr=subprocess.STDOUT, start_new_session=True)
    (TMP / "shell.pid").write_text(str(proc.pid))
    url = tunnel(8022, TMP / "shell-tunnel.pid", "shell-tunnel.log")
    state(web_shell_url=url, web_shell_user="kaggle")
    return url


def idle_watchdog(proc):
    while not STOP.is_set() and proc.poll() is None:
        idle = time.monotonic() - LAST_TOUCH
        if READY.is_set() and idle > IDLE_TIMEOUT:
            request_shutdown("idle_timeout")
            return
        time.sleep(5)


def main():
    global CONTROL_URL, LAST_TOUCH
    try:
        state(status="starting", started_at=now())
        run_logged("nvidia_smi", ["nvidia-smi"], "nvidia-smi.log", check=False)
        gpu_check()
        ensure_cloudflared()
        start_http_server(8766, LogHandler)
        log_url = tunnel(8766, TMP / "log-tunnel.pid", "log-tunnel.log")
        start_http_server(8767, ControlHandler)
        CONTROL_URL = tunnel(8767, TMP / "control-tunnel.pid", "control-tunnel.log")
        state(status="observability_ready", log_url=log_url, control_url=CONTROL_URL)
        start_optional_shell()
        ensure_runtime_bundle()

        errors = []
        proc = None
        for quant, filename in parse_model_files():
            for ctx in CTX_SIZES:
                try:
                    proc = start_model(quant, filename, ctx)
                    break
                except Exception as exc:
                    errors.append({"quant": quant, "ctx": ctx, "error": str(exc)})
                    event("model_attempt_failed", quant=quant, ctx=ctx, error=str(exc))
                    kill_pidfile(TMP / "llama.pid")
            if proc is not None and proc.poll() is None:
                break
        if proc is None or proc.poll() is not None:
            raise RuntimeError(json.dumps(errors, indent=2))

        api_url = tunnel(PORT, TMP / "api-tunnel.pid", "api-tunnel.log")
        base_url = api_url.rstrip("/") + "/v1"
        LAST_TOUCH = time.monotonic()
        state(status="ready", public_url=api_url, base_url=base_url, log_url=log_url, control_url=CONTROL_URL, idle_timeout_seconds=IDLE_TIMEOUT)
        event("ready", public_url=api_url, base_url=base_url, log_url=log_url, control_url=CONTROL_URL, model=MODEL_ALIAS)
        threading.Thread(target=idle_watchdog, args=(proc,), daemon=True).start()
        while proc.poll() is None and not STOP.is_set():
            event("ready_keepalive", idle_seconds=int(time.monotonic() - LAST_TOUCH), base_url=base_url)
            time.sleep(60)
        if not STOP.is_set():
            raise RuntimeError("llama-server stopped: " + tail(LOGS / "llama.log"))
    except Exception as exc:
        event("error", error=str(exc))
        state(status="error", error=str(exc))
        raise
    finally:
        stop_processes()


if __name__ == "__main__":
    main()
