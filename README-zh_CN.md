# netpoisson

`netpoisson` 是面向 TCP、UDP 与 SSH stdio 的泊松流量测试器。
客户端按绝对单调时钟调度发出请求；服务器应答 ECHO 与 STATUS。
实时 Req/Resp 时间线、JSON 报告和 Web 仪表盘可观察队列、RTT、丢包、带宽与调度偏差。

```bash
netpoisson [选项]... [服务器]
```

没有 `服务器`，或加上 `-d`，进入监听。否则连接到 `服务器`（`主机` 或 `主机:端口`）。
用 `--ssh` 走 SSH stdio 路径。

## 协议概要

- **TCP / UDP**：带长度前缀的二进制 PDU（version 2）。ECHO 响应可附遥测尾；
  STATUS 带回队列与最新优先的时间片窗口。
- **SSH stdio**：不以二进制 PDU 传输，而是 `@nPoi ` 前缀的 NDJSON 行。
- 客户端校验序号对应、载荷一致、重复/乱序、UDP 丢包、RTT，以及相对计划泊松时刻的发送偏差。

完整字段说明见英文 `README.md`。

## 选项（摘要）

```text
模式：-d / --stdio-server / --ssh / --install|--remove
传输：-H -p -u --tls --psk --cert/--key --tls-ca --tls-insecure
流量：-l --payload --connections --max-pending --status-interval
      --bucket --window --seed --profile
执行：-s -w[=ADDR] --no-open --json --report
```

默认端口 `1871`；Web 默认 `127.0.0.1:3871`。详情见 `netpoisson -h`。

## 时间线

默认 bucket `100 ms`，窗口 `12 s`（`--window` 也可为 **auto**）。
紧凑 TTY 显示两行；Web 显示四行：Req / Recv / Resp / Ack。

## Web 仪表盘

`-w` 提供实时页面：四条时间线、RTT/抖动图、分析说明、
**netpoisson 引起的带宽**，以及 **会话收发字节**。

页头在 **Capture**（实时 Runnings / Raw events）与 **Analyse**（统计、图表与说明）
之间切换。语言与主题也在页头；默认英文与 **Dark X-Files**（`dark-x-files`），
均写入 `localStorage`。主题来自 WorldMan，生成到 `src/web_themes/`。
仪表盘文案（含 Analysis notes）在 `src/web_i18n/<locale>.json`，不走 gettext/`po/`。

![Capture 视图：Raw events 三维与底部 Live stats](screenshot/capture.png)

*Capture* — Runnings / Raw events 铺满视口，Live stats 固定在底部。
视图：Front（X-Z）、Top Down / Spectrum（X-Y，Z 用色图）；可选透视（默认正交）、
Z 对数刻度、Fit + Keep aspect、Swing ±5–20°。Free 3D：左键旋转 / 滚轮缩放 / 中键平移。

![Analyse 视图：统计、RTT 图与分析说明](screenshot/analyse.png)

*Analyse* — 实际 req/s、丢包、RTT 分位、RFC 3550 抖动、带宽、会话字节、时间线滞后，
以及 RTT / 队列 / 直方图与文字分析。

`--window` 默认 **auto**。**dense**（Runnings 0.25×/0.5×默认/1×/2×；Raw 0.5×/1×默认/2×）按
`default × dense` 协商 timeline window。可实时改 λ / bucket / status
（`POST /api/config`）；支持暂停/继续。

```bash
./scripts/regen-web-themes.sh
```

## TLS 与 UDP 密封

```bash
netpoisson -d --tls
netpoisson --tls --tls-insecure -l 100 -s 10 127.0.0.1

netpoisson -d -u --tls --psk secret
netpoisson -u --tls --psk secret -l 100 -s 10 127.0.0.1
```

## SSH stdio

```bash
# 远端
netpoisson --stdio-server

# 本机
netpoisson --ssh user@server \
  --remote-command 'netpoisson --stdio-server' \
  -l 100 -s 60
```

配置文件：`--profile ssh-interactive` 与 `--profile ssh-bulk`。

## 示例

```bash
netpoisson -d
netpoisson -l 100 --payload 64 127.0.0.1
netpoisson -u -l 50 -s 10 127.0.0.1
netpoisson -w -l 100 127.0.0.1
netpoisson --ssh user@host --profile ssh-interactive -s 120
```

`-s` 把报告写到标准输出。实时两行写到标准错误；`-q` 关掉它们。

## 构建与测试

```bash
sudo apt install meson ninja-build python3 gettext asciidoctor
meson setup /build
ninja -C /build
meson test -C /build
```

Meson 在 `bindir` 安装 bash 启动器（由 `netpoisson.in` 生成），
Python 模块装到 `$prefix/lib/netpoisson/`。

## 国际化

CLI / 终端用 gettext：翻译在 `po/`，用 `ninja -C /build posync` 同步。英文是 msgid
源语言，`po/LINGUAS` 列出译文。

```bash
LANGUAGE=zh_CN /build/netpoisson -h
```

Web 仪表盘不经过 gettext：文案在 `src/web_i18n/<locale>.json`。

## 许可证

Copyright (C) 2026 Lenik <netpoisson@bodz.net>

采用 **AGPL-3.0-or-later** 许可。
本项目明确反对 AI 剥削与 AI 霸权，反对无脑 MIT 式许可证和政治愚蠢的 BSD 式许可证。
全文见 `LICENSE`。
