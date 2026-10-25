# SmartSync
Local-first encrypted sync with dedup, versioning, crash recovery, OS-style scheduling and a local-LLM search.

Windows PowerShell setup from the extracted project folder:

    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    powershell -ExecutionPolicy Bypass -File .\install_ollama.ps1
    powershell -ExecutionPolicy Bypass -File .\start_smartsync.ps1

Open http://127.0.0.1:8000. The start script prompts privately for the stable encryption passphrase and dashboard credentials, and binds to loopback so it is reachable only from this PC.

`install_ollama.ps1` installs Ollama from its official Windows installer when needed and downloads `llama3.2:3b`; first model download is about 2 GB. Ollama is local and its API remains on `localhost:11434`. Search, conflict summaries, and cleanup advice use that local model, with rule-based fallbacks if Ollama is unavailable.

Dashboard APIs use HTTP Basic login. The Windows start script prompts for credentials instead of using a default password.

Storage: local filesystem remains the default. To use MinIO set `SS_STORAGE=minio`, `SS_MINIO_ENDPOINT`, `SS_MINIO_ACCESS_KEY`, `SS_MINIO_SECRET_KEY`, and optionally `SS_MINIO_BUCKET` / `SS_MINIO_SECURE`.

Cloud change intake: an authenticated remote sync client posts JSON to `/api/cloud/changes` with `path`, optional `base_hash`, and base64 `content_base64`. The server compares the incoming content hash with the local and base hashes, fast-forwards when safe, or stores a conflict. `GET /api/conflicts` lists conflicts; `POST /api/conflicts/{id}/resolve?choice=keep_local|keep_cloud|keep_both` resolves them. `keep_local` adds an upload action to `/api/cloud/outbox`; the remote client acknowledges it with `POST /api/cloud/outbox/{id}/ack`. Cloud clients should only acknowledge after uploading the referenced content. `GET /api/advice` returns LLM-assisted (Ollama when reachable) cleanup and cold-file suggestions, with local heuristics as fallback.

Uploads use one-MiB independently encrypted blocks, and restores stream blocks back to disk. Block hashes deduplicate identical blocks across versions.

Benchmark: `python -m smartsync.benchmark` runs the deterministic FCFS/SJF scheduling simulation, dedup storage measurement, and 50-question text-to-SQL rule baseline. Its output is a micro-benchmark, not a claim about production throughput.

Env vars: SS_WATCH, SS_CLOUD, SS_DB, SS_PASS, SS_USER, SS_LOGIN_PASS, SS_STORAGE, SS_MINIO_*, SS_OLLAMA_HOST, SS_LLM_MODEL.

Official Ollama sources: [Windows installation](https://ollama.com/download/windows) and [Llama 3.2 model](https://ollama.com/library/llama3.2).
