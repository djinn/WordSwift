# Building the Debian Package

How to build `wordswift_0.2.0_all.deb`, the packaging decisions behind it, and
the verified install/upgrade/remove/purge behaviour.

---

## Table of Contents

- [Quick Build](#quick-build)
- [Package Layout](#package-layout)
- [Packaging Decisions](#packaging-decisions)
- [Source Files](#source-files)
- [Lifecycle Behaviour](#lifecycle-behaviour)
- [Verification](#verification)
- [Gotchas](#gotchas)

---

## Quick Build

```bash
sudo apt-get install -y debhelper dh-python lintian
dpkg-buildpackage -us -uc -b
```

The `.deb` lands in the **parent directory** (`../wordswift_0.2.0_all.deb`), as
is standard for `dpkg-buildpackage`.

Then lint:

```bash
lintian --tag-display-limit 0 ../wordswift_0.2.0_all.deb
```

> **If `apt-get purge`/`remove` hangs**, it is almost certainly Ubuntu's
> `needrestart` opening an interactive dialog with no TTY available, not a
> packaging bug. Use:
> ```bash
> sudo NEEDRESTART_MODE=a DEBIAN_FRONTEND=noninteractive apt-get purge -y wordswift
> ```

---

## Package Layout

```
wordswift_0.2.0_all.deb
├── etc/wordswift/wordswift.env              # conffile (survives upgrades)
├── usr/bin/wordswift                        # symlink → ../lib/wordswift/wordswift
├── usr/lib/wordswift/
│   ├── wordswift                            # console script (0755)
│   └── src/                                 # Python sources
│       ├── __init__.py  __main__.py  api.py
│       ├── dictionary.py engine.py  indexer.py  storage.py
├── usr/lib/systemd/system/wordswift.service
├── usr/lib/sysusers.d/wordswift.conf
├── usr/lib/tmpfiles.d/wordswift.conf
├── usr/share/doc/wordswift/{changelog.gz,copyright}
└── usr/share/man/man1/wordswift.1.gz
```

Installed size is ~124 KB. `Architecture: all` — the package contains no compiled
code, so one `.deb` serves every architecture.

Notably **absent**: `/var/lib/wordswift`. See [below](#state-directory-is-not-shipped).

---

## Packaging Decisions

### Private lib directory instead of site-packages

The upstream tree's Python package is literally named `src`, and every module
imports it absolutely (`from src.engine import ...`, `from src.api import ...`).

Installing that with `dh_python3` would place a top-level module called **`src`**
into `site-packages`, where it would collide with any other package doing the
same. So instead everything goes into `/usr/lib/wordswift` and `/usr/bin/wordswift`
is a symlink to the script inside it.

This works because the console script bootstraps `sys.path` from its own resolved
location:

```python
_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))
```

`.resolve()` matters — it means the symlink in `/usr/bin` still finds the real
directory in `/usr/lib/wordswift` and hence its sibling `src` package. Verified:
the CLI runs correctly from a read-only working directory such as `/`.

### `--buildsystem=none`

The build stages files by hand in `override_dh_auto_install`. Letting debhelper
pick a buildsystem selects `distutils`/`pybuild`, and the distutils path was
**removed in compat 12**, which makes even `dh clean` fail:

```
dh_auto_clean: error: This feature was removed in compat 12.
```

### `dh_installsysusers` is an addon

It is not part of the default sequence, so `debian/wordswift.sysusers` is
silently ignored unless you add it explicitly:

```makefile
dh $@ --buildsystem=none --with installsysusers
```

Note the addon name is `installsysusers`, not `sysusers` — using the latter
fails with `unable to load addon sysusers`.

### State directory is not shipped

`/var/lib/wordswift` is created by `tmpfiles.d` at install time and by
`StateDirectory=wordswift` at service start, both of which set the owner to the
`wordswift` user.

Shipping it in the `.deb` would make dpkg create it **root-owned**, and the
service user could not write indexes to it. This was an actual bug found during
verification, not a hypothetical.

### Dependencies

```
Depends: systemd | systemd-standalone-sysusers | systemd-sysusers,
         python3 (>= 3.10),
         python3-fastapi, python3-uvicorn,
         python3-numpy, python3-plyvel
```

- `python3-plyvel` is a **hard** dependency, not a `Recommends`. Without LevelDB
  bindings the storage layer falls back to in-memory dictionaries, indexes do not
  persist across restarts, and the only symptom is a `RuntimeWarning` in the
  journal. That is too quiet a failure to make optional.
- The `systemd | systemd-standalone-sysusers | systemd-sysusers` alternative is
  injected automatically by `dh_installsysusers`.
- `python3-multipart` is deliberately **not** a dependency. No endpoint uses
  FastAPI's `Form`/`File`/`UploadFile`, so it is not needed at runtime, and it is
  listed in `requirements.txt` only for upstream development convenience.

### Native format

`debian/source/format` is `3.0 (native)`, so the version is `0.2.0` with **no**
Debian revision. A native package cannot carry a revision — `dpkg-source` warns
about it directly:

```
dpkg-source: warning: native package version may not have a revision
```

Writing `wordswift (0.2.0-1)` in the changelog is therefore wrong for this format.

---

## Source Files

Everything hand-written lives in `debian/` and `packaging/`:

| File | Role |
|------|------|
| `debian/control` | Source/binary package metadata and dependencies |
| `debian/rules` | Build rules; stages files in `override_dh_auto_install` |
| `debian/changelog` | Version history; `dpkg-parsechangelog` reads it |
| `debian/copyright` | DEP-5 copyright; MIT for sources, BSD-3-Clause for the vendored LevelDB headers |
| `debian/postrm` | Removes `/var/lib/wordswift` on purge |
| `debian/wordswift.1` | Man page |
| `debian/wordswift.manpages` | Tells `dh_installman` about the man page |
| `debian/source/format` | `3.0 (native)` |
| `packaging/systemd/*` | Unit, env, sysusers and tmpfiles snippets |

The `packaging/systemd/` files are the canonical source and `debian/rules` copies
them into place as `debian/wordswift.{service,sysusers,tmpfiles}` just before the
relevant `dh_*` helper runs, then removes them in `override_dh_clean`. This keeps
the source tree clean for `dpkg-source` while letting non-Debian distros use the
same files directly.

Generated files (`debian/.debhelper/`, `debian/files`, `debian/wordswift/`, the
staged copies, `*.deb`) are gitignored.

---

## Lifecycle Behaviour

Verified end-to-end on Ubuntu 24.04:

| Action | `/var/lib/wordswift` | `/etc/wordswift/wordswift.env` | Service |
|--------|---------------------|-------------------------------|---------|
| **install** | created, owned `wordswift:wordswift` 0750 | installed | enabled + started |
| **upgrade** | preserved | preserved (conffile) | restarted |
| **remove** | **preserved** | removed (unmodified conffile) | stopped, disabled |
| **reinstall** | **recovered** — data immediately searchable | reinstalled | enabled + started |
| **purge** | **removed** | removed | disabled |

`remove` keeps the indexes so a reinstall or upgrade does not lose data — that is
Debian policy for `/var/lib` state. `purge` removes everything the package
created, which policy requires.

The `wordswift` system user and group are **intentionally left in place** on
purge. Deleting a system account risks its UID being reused by an unrelated
package later, which would silently reassign ownership of any files still
carrying the old UID. Removing it manually is documented in `postrm`:

```bash
sudo userdel wordswift && sudo groupdel wordswift
```

---

## Verification

Checks performed against the built package:

```bash
# Static
lintian --tag-display-limit 0 wordswift_0.2.0_all.deb     # clean, 0 warnings
systemd-analyze verify /usr/lib/systemd/system/wordswift.service
sh -n <(dpkg-deb -e wordswift_0.2.0_all.deb /tmp/c && cat /tmp/c/postrm)
man --warnings -l usr/share/man/man1/wordswift.1.gz       # renders, no warnings

# Runtime
systemctl is-active wordswift                             # active
curl http://127.0.0.1:7700/health                         # {"status":"available",...}
systemctl show wordswift -p NoNewPrivileges -p PrivateTmp -p User
                                                          # yes / yes / wordswift
```

Functional checks: index creation, document add, exact search, **typo-tolerant**
search (`waterprof jaket` → *Waterproof Jacket*), restart persistence, clean
stop/start with no stale LevelDB locks, data survival across upgrade and
reinstall, and full removal on purge.

---

## Gotchas

### Do not write the debhelper token in a comment

`dh_installdeb` replaces **every** occurrence of the substitution token, not just
a standalone line. An early version of `postrm` mentioned it inside a comment
explaining what it did, which split the comment and left stray text the shell
tried to execute as a command:

```sh
 token below is required: dh substitutes its generated
```

The fix is to never spell the token anywhere except the single standalone line
where substitution is wanted. `postrm` now warns about this in a comment that
avoids the literal.

Always syntax-check generated maintainer scripts:

```bash
dpkg-deb -e wordswift_0.2.0_all.deb /tmp/ctl && sh -n /tmp/ctl/postrm
```

### `dh_installman` needs a `.manpages` file

It does **not** auto-detect `debian/<package>.1`. Without `debian/wordswift.manpages`
listing the man page, `dh_installman` runs, does nothing, and the man page is
silently omitted — lintian then flags `no-manual-page`. The `.manpages` file is
required.

### Version must match the source format

With `3.0 (native)`, `debian/changelog` must say `wordswift (0.2.0)`. Writing
`0.2.0-1` is invalid for a native package.

### Importing `src.api` must stay side-effect free

The console script imports `src.api`, which used to construct an `IndexRegistry`
at module scope. That constructor calls `mkdir()` on the data directory, so
merely running `wordswift --help` from a read-only working directory raised
`PermissionError` before argparse could print anything — which would have shipped
a broken CLI.

The registry is now built lazily via a proxy, and `run_server()` attaches the real
one. Keep it that way: **importing this module must not touch the filesystem.**

---

## Next Steps

- **[SYSTEMD.md](SYSTEMD.md)** — service configuration, sandboxing and operations.
- **[DOCKER.md](DOCKER.md)** — container deployment as an alternative.
