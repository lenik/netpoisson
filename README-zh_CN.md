# netpoisson

`netpoisson` 按泊松过程发送请求，也可以监听这些请求。
ECHO 的响应与请求字节相同。STATUS 的响应带回服务器时间线，以及接收队列和发送队列。

```bash
netpoisson [选项]... [服务器]
```

没有 `服务器`，或加上 `-d`，就进入监听。否则连接到 `服务器`（`主机` 或 `主机:端口`）。

## 选项

- `-d`, `--daemon` — 监听（省略服务器时也是默认行为）
- `-H`, `--host HOST` — 绑定地址（默认 `0.0.0.0`）
- `-p`, `--port PORT` — 绑定或连接端口（默认 `1871`）
- `-u`, `--udp` — 用 UDP，默认是 TCP
- `-l`, `--lambda N` — 每秒发出的请求数（默认 `100`）
- `-s`, `--stats 秒` — 运行若干秒后打印 JSON 报告
- `-w`, `--web` — 提供实时仪表盘并打开浏览器
- `--web-host`, `--web-port` — 仪表盘绑定地址（默认 `127.0.0.1:3871`）
- `-v`, `--quiet`, `-h`, `--version`

## 两行状态

时间片是 100 毫秒。最新的一片在左边，两行随时间向右滚动。

```text
Req. [1 2 0 1 1 3 1 0 2 1 3 1 -> 17 -> 1 2 0 1 1 3 1 0 2 1 3 1]
Resp [                     16 -> 20 -> 2 0 1 1 3 1 0 2 1 3 1]
```

- 请求行上的数字是本机每个时间片的到达数。
- `17` 是本机发送队列：这些请求还没有离开网口。
- 响应行上的数字是服务器每个时间片的响应数。最近的时间片在响应出现之前留空。
- `16` 是服务器还没有读取或分析的请求。
- `20` 是服务器还没离开网口的响应。

`-w` 在浏览器里显示同样的时间线，并实时分析 RTT、RFC 3550 抖动、丢失、排队时延，以及到达间隔是否仍像泊松过程。
仪表盘使用 WorldMan 主题，默认 **Dark X-Files**（`dark-x-files`），可在页头切换。
`--window` 默认 **auto**（随布局缩放）；**dense**（Runnings 0.25×/0.5×默认/1×/2×；Raw 0.5×/1×默认/2×）按
`default × dense` 协商 timeline window（X 铺满宽、Y 铺满高）；可实时改 λ / bucket / status
（`POST /api/config`）；支持暂停/继续，以及 Runnings / Raw events（三维散点·网格·曲面）。

## 示例

```bash
netpoisson -d
netpoisson -l 100 127.0.0.1
netpoisson -u -l 50 -s 10 127.0.0.1
netpoisson -w -l 100 127.0.0.1
```

`-s` 把报告写到标准输出。实时两行写到标准错误；`-q` 关掉它们。

## 构建与测试

```bash
sudo apt install meson ninja-build python3 gettext asciidoctor
meson setup /build
ninja -C /build
meson test -C /build
```

程序只用 Python 3 标准库。Meson 在 `bindir` 只安装 bash 启动器（由 `netpoisson.in` 生成），
Python 模块装到 `/usr/lib/netpoisson/`，启动器设置 `PYTHONPATH` 后执行。

## 国际化

CLI / 终端用 gettext：翻译在 `po/`，用 `ninja -C /build posync` 同步。英文是 msgid
源语言，`po/LINGUAS` 列出译文。

```bash
LANGUAGE=zh_CN /build/netpoisson -h
```

Web 仪表盘不经过 gettext：文案在 `src/web_i18n/<locale>.json`（含 Analysis notes
模板），由页面内嵌 JSON 切换语言。

## 许可证

Copyright (C) 2026 Lenik <netpoisson@bodz.net>

采用 **AGPL-3.0-or-later** 许可。
本项目明确反对 AI 剥削与 AI 霸权，反对无脑 MIT 式许可证和政治愚蠢的 BSD 式许可证。
全文见 `LICENSE`。
