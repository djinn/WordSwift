# Running WordSwift under systemd

WordSwift ships systemd integration files that create a dedicated service user,
provision its state directory, and run the server in a locked-down sandbox.

The packaged `.deb` installs all of this automatically. This guide covers how it
works and how to set it up by hand on any systemd distribution.

---

## Table of Contents

- [Files](#files)
- [Quick Start (Debian package)](#quick-start-debian-package)
- [Manual Installation](#manual-installation)
- [Configuration](#configuration)
- [Operations](#operations)
- [Sandboxing](#sandboxing)
- [Why KillMode=process](#why-killmodeprocess)
- [Single-Writer Constraint](#single-writer-constraint)
- [Troubleshooting](#troubleshooting)

---

## Files

Everything lives in [`packaging/systemd/`](../packaging/systemd/):

| File | Installs to | Purpose |
|------|-------------|---------|
| `wordswift.service` | `/usr/lib/systemd/system/wordswift.service` | The unit itself |
| `wordswift.env` | `/etc/wordswift/wordswift.env` | Optional site overrides (a conffile) |
| `wordswift.sysusers.conf` | `/usr/lib/sysusers.d/wordswift.conf` | Creates the `wordswift` user |
| `wordswift.tmpfiles.conf` | `/usr/lib/tmpfiles.d/wordswift.conf` | Creates `/var/lib/wordswift` with correct ownership |

The unit is **enabled and started automatically** on a fresh install, and
**restarted** on upgrade. This is debhelper's generated `postinst` behaviour
(`deb-systemd-invoke start` for new installs, `restart` for upgrades), not
something this package opts into.

That means the API is live on `127.0.0.1:7700` immediately after `apt install`.
Because the default bind address is loopback-only, nothing is exposed externally
— but if you plan to set `WORDSWIFT_HOST=0.0.0.0`, edit
`/etc/wordswift/wordswift.env` *before* installing, or be aware that the service
is already reachable locally the moment installation finishes.

---

## Quick Start (Debian package)

```bash
sudo apt install ./wordswift_0.2.0_all.deb

# Verify
systemctl status wordswift
curl http://127.0.0.1:7700/health
# {"status":"available","indexes":0}
```

Then create an index and start searching:

```bash
curl -X POST http://127.0.0.1:7700/indexes \
  -H "Content-Type: application/json" \
  -d '{"uid":"products","dim":100}'

curl -X POST http://127.0.0.1:7700/indexes/products/documents \
  -H "Content-Type: application/json" \
  -d '[{"id":1,"title":"Patagonia Torrentshell Waterproof Jacket"}]'

curl "http://127.0.0.1:7700/indexes/products/search?q=waterprof+jaket"
```

Indexes are written to `/var/lib/wordswift` and survive restarts, upgrades and
reinstalls.

---

## Manual Installation

For non-Debian systemd distributions:

```bash
# 1. System user
sudo systemd-sysusers --create packaging/systemd/wordswift.sysusers.conf
# or manually:
#   sudo useradd --system --home-dir /var/lib/wordswift \
#                --shell /usr/sbin/nologin wordswift

# 2. State directory
sudo install -d -o wordswift -g wordswift -m 0750 /var/lib/wordswift
sudo install -d -m 0755 /etc/wordswift

# 3. Install the application
sudo install -d /usr/lib/wordswift
sudo cp -r src /usr/lib/wordswift/src
sudo install -m 0755 wordswift /usr/lib/wordswift/wordswift
sudo ln -s ../lib/wordswift/wordswift /usr/bin/wordswift

# 4. Unit + config
sudo install -m 0644 packaging/systemd/wordswift.service \
     /usr/lib/systemd/system/wordswift.service
sudo install -m 0644 packaging/systemd/wordswift.env \
     /etc/wordswift/wordswift.env

# 5. Activate
sudo systemctl daemon-reload
sudo systemctl enable --now wordswift.service
```

> The console script locates its sibling `src` package through a `sys.path`
> bootstrap based on `Path(__file__).resolve().parent`, so it works from any
> working directory without `PYTHONPATH`. That is why the script lives *next to*
> `src/` rather than in `/usr/bin` directly.

---

## Configuration

### Environment variables

Only one variable is read by the application:

| Variable | Read by | Default |
|----------|---------|---------|
| `WORDSWIFT_DATA_DIR` | the WordSwift server | `/var/lib/wordswift` |
| `WORDSWIFT_HOST` | **the unit file only** — substituted into `ExecStart` as `--host` | `127.0.0.1` |
| `WORDSWIFT_PORT` | **the unit file only** — substituted into `ExecStart` as `--port` | `7700` |

There is no `WORDSWIFT_HOST`/`WORDSWIFT_PORT` support in the Python code — the
unit does the substitution. Both have defaults set with `Environment=` in the
unit itself, because systemd expands `${VAR}` to an empty string when a variable
is undefined, which would pass empty `--host`/`--port` arguments.

### Editing the configuration

`/etc/wordswift/wordswift.env` is a registered conffile, so local edits survive
package upgrades:

```bash
sudoedit /etc/wordswift/wordswift.env
sudo systemctl daemon-reload
sudo systemctl restart wordswift
```

> **This is a systemd `EnvironmentFile`, not a shell script.** Do not use
> `export`, do not quote values, and do not use command substitution:
>
> ```bash
> WORDSWIFT_PORT=8080        # correct
> export WORDSWIFT_PORT=8080 # wrong — systemd takes "export" literally
> WORDSWIFT_PORT="8080"      # wrong — quotes become part of the value
> ```

The `EnvironmentFile=-/etc/wordswift/wordswift.env` line uses a leading `-`, so a
missing file is not fatal.

### Changing the data directory

`ExecStart` passes `--data-dir /var/lib/wordswift` explicitly, so editing
`WORDSWIFT_DATA_DIR` alone does **not** relocate the indexes. Either edit the
unit with a drop-in:

```bash
sudo systemctl edit wordswift.service
```

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/wordswift serve --host ${WORDSWIFT_HOST} --port ${WORDSWIFT_PORT} --data-dir /srv/wordswift
StateDirectory=
StateDirectory=wordswift-alt
```

...or keep the default location and mount your storage at `/var/lib/wordswift`.

---

## Operations

```bash
systemctl status wordswift          # is it running
systemctl restart wordswift         # restart (data persists)
sudo journalctl -u wordswift -f     # follow logs

# Disk usage of indexes
sudo du -sh /var/lib/wordswift

# Inspect indexes as the service user (state dir is mode 0750)
sudo -u wordswift wordswift list-indexes --data-dir /var/lib/wordswift
```

> **`list-indexes` is intentionally limited.** Counting documents requires
> opening the LevelDB directories, which needs an exclusive lock the running
> server already holds. The command therefore reads `manifest.json` and reports
> each index's dimension plus whether its model, vector store and full-text store
> are present — without touching the locks:
>
> ```
> Name                 Dim    Model   Vectors   Full-text
> ------------------------------------------------------------
> products             100    -       yes       yes
> ```
>
> For live document counts use the HTTP API instead:
> `curl http://127.0.0.1:7700/indexes/products/stats`

The CLI exits non-zero on failure, so it is usable in scripts:

```bash
wordswift list-indexes --data-dir /var/lib/wordswift || echo "failed"
```

---

## Sandboxing

The unit enables a restrictive set of protections, verified active with:

```bash
systemctl show wordswift.service \
  -p NoNewPrivileges -p PrivateTmp -p ProtectSystem \
  -p ProtectHome -p StateDirectory -p User -p UMask
```

Confirmed values:

```
NoNewPrivileges=yes     PrivateTmp=yes     ProtectSystem=full
ProtectHome=yes         StateDirectory=wordswift
User=wordswift          Group=wordswift    UMask=0027
KillMode=process
```

Plus `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectControlGroups`,
`RestrictSUIDSGID`, `RestrictNamespaces`, `LockPersonality`, `ProtectHostname`,
`ProtectClock`, `RestrictRealtime`, an empty `CapabilityBoundingSet`,
`SystemCallFilter=@system-service` with `@privileged`/`@resources` denied, and
`SystemCallArchitectures=native`.

Everything is read-only except `/var/lib/wordswift`, which `StateDirectory=` makes
writable for the service user.

`UMask=0027` means new index files are `0640` and directories `0750` — readable
by the service group but not world-readable. This matters because indexes contain
your catalog content.

### Security warning

> ⚠️ **WordSwift has no authentication and enables CORS for all origins.**
> The unit therefore binds to `127.0.0.1` by default. Do not set
> `WORDSWIFT_HOST=0.0.0.0` without a reverse proxy enforcing auth — the write
> endpoints (`POST /indexes`, `POST .../documents`, `DELETE /indexes/{name}`)
> would be open to anyone who can reach the host. See
> [DOCKER.md](DOCKER.md#production-hardening) for an nginx example.

---

## Why KillMode=process

The unit sets `KillMode=process` and `TimeoutStopSec=30s` deliberately.

LevelDB indexes are directories holding an **exclusive lock**. systemd's default
`KillMode=control-group` sends `SIGTERM` to every process in the cgroup and
follows up with `SIGKILL` after the timeout. A `SIGKILL`ed server cannot close
its LevelDB handles, which leaves a stale `LOCK` file behind — and the next start
fails with `IO error: lock .../LOCK: Resource temporarily unavailable`.

`KillMode=process` signals only the main process and lets it shut down cleanly,
releasing the locks. Verified: a stop/start cycle produces no lock errors.

```bash
sudo systemctl stop wordswift     # clean shutdown, locks released
sudo systemctl start wordswift    # reopens without error
```

---

## Single-Writer Constraint

**Only one process may use a given data directory at a time.** LevelDB enforces
this with an exclusive lock.

This is the single most common operational mistake. Do not run a foreground
server against the live state directory while the systemd service is running:

```bash
# BROKEN — the service already holds these locks
wordswift serve --data-dir /var/lib/wordswift

# RuntimeError: LevelDB installed but failed to open '/var/lib/wordswift/...':
#   b'IO error: lock ...: Resource temporarily unavailable'
```

The error message is intentional. Earlier versions silently fell back to an empty
in-memory index, which served blank results and looked like data loss. Now the
failure is loud and names the cause.

To work on the live data, stop the service first:

```bash
sudo systemctl stop wordswift
wordswift serve --data-dir /var/lib/wordswift
sudo systemctl start wordswift
```

Or copy the directory and work on the copy. The same constraint is why the
container docs warn against scaling replicas over a shared volume — see
[DOCKER.md](DOCKER.md#read-scaling).

---

## Troubleshooting

### Service fails to start with a lock error

Another process owns the data directory. Check for a stray foreground server:

```bash
sudo systemctl status wordswift          # is it already running?
pgrep -af "wordswift serve"              # any manual instances?
sudo lsof +D /var/lib/wordswift 2>/dev/null | head
```

Kill the other process, then start the service. If a previous `SIGKILL` left a
stale lock and you are certain nothing is running, remove the `LOCK` files:

```bash
sudo systemctl stop wordswift
sudo find /var/lib/wordswift -name LOCK -delete
sudo systemctl start wordswift
```

### Permission denied on /var/lib/wordswift

The directory must be owned by the service user:

```bash
sudo ls -ld /var/lib/wordswift
# drwxr-x--- 2 wordswift wordswift ...

# Repair ownership and let tmpfiles.d fix the mode:
sudo chown -R wordswift:wordswift /var/lib/wordswift
sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/wordswift.conf
```

### No LevelDB persistence (indexes vanish on restart)

If `python3-plyvel` is missing, the storage layer falls back to in-memory
dictionaries and warns at startup:

```
RuntimeWarning: LevelDB unavailable; '...' uses in-memory storage
and will NOT persist across restarts
```

Check the journal for it — the package depends on `python3-plyvel` precisely to
prevent this:

```bash
sudo journalctl -u wordswift --no-pager | grep -i "LevelDB unavailable"
dpkg -l python3-plyvel
```

Healthy startup logs show `LevelDB index opened at /var/lib/wordswift/<name>_index`.

### `list-indexes` says permission denied

The state directory is mode `0750` owned by `wordswift`, so run it as that user:

```bash
sudo -u wordswift wordswift list-indexes --data-dir /var/lib/wordswift
```

The CLI prints this hint rather than a traceback.

### apt/dpkg hangs during install or remove

Unrelated to WordSwift: Ubuntu's `needrestart` can open an interactive dialog
asking which services to restart, which blocks forever when there is no TTY. Run
non-interactively:

```bash
sudo NEEDRESTART_MODE=a DEBIAN_FRONTEND=noninteractive apt install ./wordswift_0.2.0_all.deb
```

---

## Next Steps

- **[PACKAGING.md](PACKAGING.md)** — build the `.deb`, packaging internals, and lifecycle semantics.
- **[DOCKER.md](DOCKER.md)** — container deployment as an alternative to systemd.
- **[ECOMMERCE.md](ECOMMERCE.md)** — connect a product catalog.
