# Running WordSwift in Docker & Podman

WordSwift ships with a container image that works identically under **Docker**,
**Podman**, and **podman-compose**. This guide covers building, running,
persisting indexes, and scaling.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Docker](#docker)
- [Podman](#podman)
- [Rootless Podman Notes](#rootless-podman-notes)
- [Persisting Your Indexes](#persisting-your-indexes)
- [Environment Variables](#environment-variables)
- [Kubernetes / Pod Definition](#kubernetes--pod-definition)
- [Production Hardening](#production-hardening)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# Clone and build
git clone https://github.com/djinn/WordSwift.git
cd WordSwift

# Start with Docker Compose
docker compose up -d

# ...or with Podman
podman-compose up -d

# Verify it's healthy
curl http://localhost:7700/health
# {"status":"available","indexes":0}
```

Your API is now live on **port 7700**.

---

## Docker

### Build the image

```bash
docker build -t wordswift:0.2.0 .

# Tag it for a registry
docker tag wordswift:0.2.0 ghcr.io/djinn/wordswift:0.2.0
```

### Run standalone

```bash
docker run -d \
  --name wordswift \
  -p 7700:7700 \
  -v wordswift_data:/app/data \
  --restart unless-stopped \
  wordswift:0.2.0
```

### Inspect logs

```bash
docker logs -f wordswift
docker exec -it wordswift ls -la /app/data
```

### Override the command

The image's `ENTRYPOINT` is `python -m src` and the default `CMD` supplies
host/port/data-dir. Override the `CMD` to change any of them:

```bash
# Custom port and data dir
docker run -d -p 8080:8080 wordswift:0.2.0 \
  --host 0.0.0.0 --port 8080 --data-dir /app/data
```

Because the entrypoint is fixed, running a different program requires
overriding it explicitly:

```bash
# Train a corpus instead of serving
docker run --rm -v wordswift_data:/app/data \
  --entrypoint python wordswift:0.2.0 \
  wordswift train /app/corpus/corpus.txt -o /app/data/vectors.ws

# Inspect the image
docker run --rm -it --entrypoint bash wordswift:0.2.0
```

> ⚠️ **Do not run `python -m src.api`.** That executes the module body twice
> (once via `src/__init__.py`, once as `__main__`), building two index
> registries that fight over the LevelDB locks — the second silently loses and
> serves an empty index. Always use `python -m src`.

---

## Podman

Podman is daemonless and drop-in compatible with the Docker CLI.

### Build and run

```bash
podman build -t wordswift:0.2.0 .

podman run -d \
  --name wordswift \
  -p 7700:7700 \
  -v wordswift_data:/app/data \
  wordswift:0.2.0
```

### podman-compose

```bash
# Install podman-compose
pip install podman-compose

# Use the same docker-compose.yml — no changes needed
podman-compose up -d
podman-compose ps
podman-compose logs -f wordswift
```

### HEALTHCHECK caveat

Podman builds OCI-format images by default, which **drop the `HEALTHCHECK`
instruction**. You'll see this warning during build:

```
level=warning msg="HEALTHCHECK is not supported for OCI image format and will be ignored.
Must use `docker` format"
```

The image still works — only the built-in health polling is skipped. Two options:

```bash
# Option A: build in Docker format so HEALTHCHECK is honoured
podman build --format docker -t wordswift:0.2.0 .

# Option B: let Podman monitor health at runtime instead
podman run -d --name wordswift -p 7700:7700 \
  --health-cmd "curl -fsS http://localhost:7700/health" \
  --health-interval 10s --health-timeout 5s --health-retries 5 \
  -v wordswift_data:/app/data wordswift:0.2.0

podman healthcheck run wordswift
```

The `docker-compose.yml` also defines a `healthcheck:` block, which Podman
honours at the compose level regardless of image format.

### Generate a systemd unit (Podman's recommended approach)

```bash
# Run the container once
podman run -d --name wordswift -p 7700:7700 \
  -v wordswift_data:/app/data wordswift:0.2.0

# Generate the unit file
podman generate systemd --new --name wordswift > container-wordswift.service

# Install and enable
mkdir -p ~/.config/systemd/user/
mv container-wordswift.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now container-wordswift.service
loginctl enable-linger $USER   # keep it running after logout
```

---

## Rootless Podman Notes

The image runs as a non-root user (`wordswift`, created in the Dockerfile),
which works cleanly under rootless Podman. A few gotchas:

| Issue | Fix |
|-------|-----|
| **Ports < 1024** (e.g. 80) | Rootless can't bind privileged ports. Map `8080:7700` and put a reverse proxy in front. |
| **Volume permission denied** | Use a named volume (`-v wordswift_data:/app/data`) rather than a host bind mount, or set `--userns=keep-id`. |
| **SELinux denials (RHEL/Fedora)** | Add `:z` or `:Z` to bind mounts: `-v ./data:/app/data:Z` |

```bash
# Rootless with a host bind mount
podman run -d --userns=keep-id -p 7700:7700 \
  -v ./data:/app/data:Z wordswift:0.2.0
```

---

## Persisting Your Indexes

WordSwift stores three artifacts per index inside the data directory:

```
/app/data/
├── manifest.json      # Registry of all indexes (name → paths)
├── shop.ws            # Trained embedding model
├── shop_index/        # LevelDB — word vectors
└── shop_ft/           # LevelDB — inverted index (BM25 + fuzzy)
```

**Always mount `/app/data` as a volume.** Without it, your indexes vanish when
the container stops.

```bash
# Named volume (managed by Docker/Podman)
docker run -v wordswift_data:/app/data wordswift:0.2.0

# Host bind mount (easy to back up)
docker run -v /srv/wordswift:/app/data wordswift:0.2.0
```

### Backup and restore

```bash
# Backup
docker run --rm -v wordswift_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/wordswift-$(date +%F).tar.gz -C /data .

# Restore
docker run --rm -v wordswift_data:/data -v $(pwd):/backup alpine \
  tar xzf /backup/wordswift-2026-10-18.tar.gz -C /data
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WORDSWIFT_DATA_DIR` | `data` | Where indexes are stored. **Set this in containers** — it's read at module import time. |
| `PYTHONUNBUFFERED` | — | Set to `1` for live log streaming |

```bash
docker run -d \
  -e WORDSWIFT_DATA_DIR=/app/data \
  -e PYTHONUNBUFFERED=1 \
  -p 7700:7700 wordswift:0.2.0
```

Host and port are **CLI arguments**, not environment variables — there is no
`WORDSWIFT_HOST` or `WORDSWIFT_PORT`. Pass them by overriding the image `CMD`:

```bash
docker run -d -p 8080:8080 wordswift:0.2.0 \
  --host 0.0.0.0 --port 8080 --data-dir /app/data
```

> **Note:** `--data-dir` and `WORDSWIFT_DATA_DIR` both work. The CLI flag takes
> precedence and is exported to the environment so worker processes agree on the
> location.

---

## Kubernetes / Pod Definition

For a Kubernetes-native deployment (also works with `podman kube play`):

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: wordswift
  labels:
    app: wordswift
spec:
  containers:
    - name: wordswift
      image: ghcr.io/djinn/wordswift:0.2.0
      ports:
        - containerPort: 7700
      env:
        - name: WORDSWIFT_DATA_DIR
          value: /app/data
      volumeMounts:
        - name: data
          mountPath: /app/data
      resources:
        requests:
          memory: "512Mi"
          cpu: "250m"
        limits:
          memory: "2Gi"
          cpu: "1000m"
      livenessProbe:
        httpGet:
          path: /health
          port: 7700
        initialDelaySeconds: 15
        periodSeconds: 10
      readinessProbe:
        httpGet:
          path: /health
          port: 7700
        initialDelaySeconds: 5
        periodSeconds: 5
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: wordswift-data
```

```bash
# Generate from a running Podman container instead of writing YAML by hand
podman generate kube wordswift > wordswift-pod.yaml
```

---

## Production Hardening

```bash
docker run -d \
  --name wordswift \
  --restart unless-stopped \
  -p 127.0.0.1:7700:7700 \        # bind to loopback only
  -v wordswift_data:/app/data \
  --memory 2g \                    # cap memory
  --cpus 2 \                       # cap CPU
  --read-only \                    # read-only root filesystem
  --tmpfs /tmp \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  wordswift:0.2.0
```

Then terminate TLS and add auth in front of it:

```nginx
server {
    listen 443 ssl http2;
    server_name search.example.com;

    ssl_certificate     /etc/letsencrypt/live/search.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/search.example.com/privkey.pem;

    # WordSwift has no built-in auth — enforce it here.
    location / {
        # Allow only your backend services
        allow 10.0.0.0/8;
        deny all;

        proxy_pass http://127.0.0.1:7700;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }
}
```

> ⚠️ **WordSwift currently enables `allow_origins=["*"]` and has no
> authentication.** Never expose port 7700 directly to the public internet.
> Put it behind a reverse proxy that enforces auth, or bind it to a private
> network only.

---

## Troubleshooting

### "LevelDB open failed: ... using fallback"

The image includes `libleveldb-dev` and `plyvel`, so this shouldn't happen in
the container. If it does, you're silently falling back to in-memory dict
storage — **data will not persist**. Check the startup logs:

```bash
docker logs wordswift | grep -i leveldb
# Good:  LevelDB index opened at /app/data/shop_index
# Bad:   LevelDB open failed: ...; using fallback
```

A common cause is a volume owned by root while the container runs as
`wordswift`. Fix with a named volume or `--userns=keep-id`.

### Indexes disappear after restart

You didn't mount `/app/data`. See [Persisting Your Indexes](#persisting-your-indexes).

### Port already in use

```bash
docker ps -a | grep 7700
WORDSWIFT_PORT=7701 docker compose up -d
```

### Container exits immediately

```bash
docker logs wordswift          # read the traceback
docker run --rm -it wordswift:0.2.0 bash   # drop into a shell
```

### Verify the data dir is actually being honoured

```bash
docker exec wordswift ls -la /app/data
# You should see manifest.json and <index>_index/ directories here,
# NOT in /app/data if you passed a different --data-dir.
```

---

## Next Steps

- **[Ecommerce integration](ECOMMERCE.md)** — hook WordSwift into Shopify, WooCommerce, BigCommerce, or Magento.
- **[RAG & agents](RAG.md)** — use WordSwift as a retrieval backend for LLM agents.
