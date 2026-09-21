# netpoisson

`netpoisson` offers a Poisson stream of requests and can listen for them.
ECHO replies are the request bytes. STATUS replies carry the server timeline
and its receive and send queues.

```bash
netpoisson [OPTION]... [SERVER]
```

With no `SERVER`, or with `-d`, it listens. Otherwise it connects to `SERVER`
(`host` or `host:port`).

## Options

- `-d`, `--daemon` — listen (also the default when `SERVER` is omitted)
- `-H`, `--host HOST` — bind address (default `0.0.0.0`)
- `-p`, `--port PORT` — bind or connect port (default `1871`)
- `-u`, `--udp` — UDP instead of TCP
- `-l`, `--lambda N` — offered requests per second (default `100`)
- `-s`, `--stats SECS` — run for `SECS` seconds, then print a JSON report
- `-w`, `--web` — serve a live dashboard and open a browser
- `--web-host`, `--web-port` — dashboard bind (default `127.0.0.1:8711`)
- `-v`, `--quiet`, `-h`, `--version`

## What the two lines mean

Time slices are 100 ms. The newest slice is on the left and the lines scroll
to the right.

```text
Req. [1 2 0 1 1 3 1 0 2 1 3 1 -> 17 -> 1 2 0 1 1 3 1 0 2 1 3 1]
Resp [                     16 -> 20 -> 2 0 1 1 3 1 0 2 1 3 1]
```

- The request numbers are local arrivals in each slice.
- `17` is the local send queue: requests that have not left this host.
- The response numbers are server replies in each slice. Recent slices stay
  blank until replies exist.
- `16` is requests the server has not read or analyzed yet.
- `20` is responses still queued on the server, not yet on the wire.

`-w` shows the same timelines in a browser, plus RTT, RFC 3550 jitter, loss,
queue delay, and whether the arrivals still look like a Poisson process.

## Examples

```bash
netpoisson -d
netpoisson -l 100 127.0.0.1
netpoisson -u -l 50 -s 10 127.0.0.1
netpoisson -w -l 100 127.0.0.1
```

`-s` writes the report to standard output. The live lines go to the terminal
on standard error, and `-q` turns them off.

## Build and test

```bash
sudo apt install meson ninja-build python3 gettext asciidoctor
meson setup /build
ninja -C /build
meson test -C /build
```

The program is Python 3 from the standard library. Meson installs the modules
next to the `netpoisson` executable so `import` works from `/usr/bin`.

## i18n (gettext)

Catalogs live in `po/`. Sync them with `ninja -C /build posync`. English is
the source language. `po/LINGUAS` lists the translations.

```bash
LANGUAGE=zh_CN /build/netpoisson -h
```

## License

Copyright (C) 2026 Lenik <netpoisson@bodz.net>

Licensed under **AGPL-3.0-or-later**.
This project explicitly opposes AI exploitation and AI hegemony, and rejects
mindless MIT-style licensing and politically naive BSD-style licensing.
See `LICENSE`.
