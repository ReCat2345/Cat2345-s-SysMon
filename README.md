# SysMon · 轻量系统监控面板

一个依赖很少（Python + psutil）的服务器监控工具：后端采集 CPU、内存、磁盘、GPU、网络指标，前端是 Material Design 3 风格的网页面板。访客打开网页即可查看面板，只有修改设置才需要管理员登录；所有指标只在本机采集，不上传任何数据。

vibe coding by deepseek v4 flash，主要是没有我自己满意的系统监控工具，所以就随手写的这玩意，readme也是ai写的，整体下来依然有不好的地方所以求大佬轻喷（

## 功能

- **五项指标**：CPU（整体 / 每核 / 频率 / 温度 / 负载）、内存（含 Swap）、磁盘（分区占用 / 读写速率）、GPU（利用率 / 显存 / 温度 / 功耗）、网络（上下行速率 / 累计流量）。
- **两种展示方式**：每个模块可以切换「波形」或「表盘」（表盘显示百分比）。网速没有百分比基准，因此只提供波形。
- **访客只读**：打开网页直接看到实时面板；模块排序、隐藏、刷新间隔、GPU 开关、密码等修改需要管理员登录。
- **模块可排序、可隐藏**：管理页面里拖动排序或用箭头调整，开关控制显示；顺序与可见性保存在服务端，所有设备一致。
- **刷新间隔可调**：默认 5 秒，范围 2–60 秒。
- **管理员登录**：PBKDF2-SHA256 密码哈希、HttpOnly 会话 Cookie、登录失败限流、跨站来源校验。
- **品牌与外观**：网页标题（≤ 40 字符）、自定义图标（同步浏览器标签页 favicon）、壁纸（PNG/JPG/WebP，透明度 0–100%）、5 套预设配色 + HEX 自定义主色。
- **卡片与顶栏配色**：默认跟随主色生成表面色，也可切换为中性或单独指定卡片颜色；文字对比度会随底色自动调整。
- **Minecraft Java 服务器**：侧边栏入口（默认关闭）、服务器列表与详情、SRV 适配、服务器编辑、网格 / 列表视图、SLP 状态、玩家皮肤头像、在线历史峰值（15 分钟–7 天及自定义范围）。
- **硬件信息**：CPU 型号、内存条规格、磁盘型号、网卡型号，按模块单独开关显示。
- **多厂商 GPU**：NVIDIA（`nvidia-smi`）、AMD（amdgpu sysfs）、Intel（`intel_gpu_top`）；都探测不到时 GPU 模块默认关闭，管理页标记「不支持」并禁用开关。
- **深色 / 浅色 / 跟随系统**，颜色使用 M3 基线令牌，卡片、按钮、开关、分段按钮均按 Material 3 规范实现。

## 快速开始

需要 Python 3.9 或更高版本。

```bash
cd sysmon
python -m pip install -r requirements.txt   # 只安装 psutil
python app.py
```

打开 <http://127.0.0.1:8088>。

对外提供服务（例如内网服务器）：

```bash
python app.py --host 0.0.0.0 --port 8088
```

其他参数：

```bash
python app.py --data-dir /var/lib/sysmon   # 自定义数据目录
python app.py --reset-password             # 忘记密码时交互式重置（需重启服务）
python app.py --version
```

### 后台运行（SSH）

`sysmon.sh` 使用 `nohup` + `setsid` 把服务放进新会话，关闭 SSH / 断线重连不会停止服务；PID 和日志分别保存在 `.sysmon.pid`、`logs/sysmon.log`。

```bash
chmod +x sysmon.sh
./sysmon.sh start
./sysmon.sh status
./sysmon.sh logs       # Ctrl+C 只退出日志查看，不影响服务
./sysmon.sh restart
./sysmon.sh stop
```

默认监听 `127.0.0.1:8088`，优先使用项目内的 `.venv/bin/python`。可用环境变量覆盖：

```bash
SYSMON_HOST=0.0.0.0 SYSMON_PORT=8088 SYSMON_DATA_DIR=/var/lib/sysmon ./sysmon.sh start
```

脚本是幂等的：已在运行时会直接提示，不会重复启动；启动失败会打印日志末尾。如果希望 SSH 登录后自动确保启动，可在 `~/.bashrc` 末尾加入：

```bash
[ -x "$HOME/sysmon/sysmon.sh" ] && "$HOME/sysmon/sysmon.sh" start >/dev/null 2>&1
```

生产环境仍然建议用 systemd（支持开机自启和异常自动重启）。

### 首次初始化

第一次启动时控制台会打印初始化令牌，同时保存在 `data/setup-token.txt`：

```
========================================================
 SysMon 1.4.1  系统监控面板
 地址:     http://127.0.0.1:8088
 管理员:   未配置（首次使用需初始化）
--------------------------------------------------------
 初始化令牌（第一次打开网页时填写）：
   xUPCiGYABM4Qz4BtNL87N2R5gNhHgS1j
========================================================
```

打开网页后填写令牌、用户名和密码即完成初始化，令牌文件会被删除。初始化之前面板仍可匿名只读访问，顶部会提示完成初始化；未初始化时任何人都不能直接创建账户，必须持有该令牌；初始化完成前不要把端口暴露到公网。

## 管理面板

登录后点击右上角的滑杆图标进入「管理与设置」：

| 项目 | 说明 |
| --- | --- |
| 品牌与外观 | 标题、图标、壁纸与透明度、5 套预设配色或自定义 HEX |
| 监控模块 | 拖动排序、箭头微调、开关隐藏；每个模块可选波形或表盘 |
| 硬件信息 | 每个模块的第二列开关，控制卡片底部是否显示 CPU / 内存 / 磁盘 / 网卡型号 |
| 刷新间隔 | 2–60 秒，默认 5 秒，所有登录设备共用 |
| 主题 | 跟随系统 / 浅色 / 深色 |
| GPU 监控 | 显示支持状态与检测原因；不支持时开关禁用；支持时可随时开关 |
| Minecraft | 启用 / 关闭 MC 服务器监控；关闭时导航按钮和侧边栏一起隐藏 |
| 账户安全 | 修改密码，修改后其他设备的会话立即失效 |

改动需要点击右上角「保存」才会写入 `data/config.json`。未保存时离开会提示。

## 品牌与外观

管理页最上方的「品牌与外观」支持：

- **网页标题**：显示在浏览器标签页和顶栏品牌位，最多 **40 个字符**，留空恢复为 `SysMon`。
- **网页图标**：PNG / JPEG / WebP / ICO，≤ 512 KB。上传后立即替换浏览器标签页的 favicon，同时用于顶栏预览。
- **网页壁纸**：PNG / JPEG / WebP，≤ 8 MB。透明度 0–100%（默认 35%），0% 相当于不显示但仍保留文件。
- **主题配色**：5 套预设（紫罗兰 / 海蓝 / 森林 / 日落橙 / 樱花粉）+ 自定义 HEX 主色（如 `#6750A4`）。配色会按浅色 / 深色模式分别派生容器色与文字色，并实时预览。
- **卡片与顶栏**：
  - `跟随主题`（默认）：由主色生成页面、顶栏、卡片、悬停层的整套表面色，切换预设时卡片和顶栏一起变化；
  - `中性`：使用中性灰表面，不受主色影响；
  - `自定义`：单独指定卡片与顶栏的底色（HEX），文字和边框对比度根据该颜色自动计算。

图标和壁纸以文件形式保存在 `data/branding/`，元信息在 `data/branding.json`；标题和配色随其他设置一起保存。图标、壁纸上传只接受经过文件头校验的位图，SVG 会被拒绝，避免同源脚本风险。

## 硬件信息

后台线程在启动时采集一次硬件型号（不阻塞 Web 服务），显示在对应卡片底部：

| 模块 | 内容 | 来源 |
| --- | --- | --- |
| CPU | 型号、物理核心数 | `/proc/cpuinfo`、Windows 注册表 |
| 内存 | 类型 / 频率 / 条数与单条容量 | Windows `Win32_PhysicalMemory`、Linux `dmidecode`、macOS `system_profiler` |
| 磁盘 | 型号、容量、SSD/HDD；多块磁盘时显示「N 块 · 展开」列表；自动排除 snap 的 squashfs/loop 镜像与 Docker overlay 等虚拟分区 | Windows `Get-PhysicalDisk`、Linux `/sys/block/*/device/model` |
| 网络 | 网卡型号或驱动、链路速率 | Windows `Get-NetAdapter`、Linux sysfs + psutil |

每个模块的硬件信息可以在管理页用第二个开关单独关闭；拿不到细节时退化为总容量、分区设备名等基础信息。

## GPU 说明

按下列顺序探测，任一采集接口可用即启用 GPU 模块：

| 厂商 | 采集方式 | 平台 | 依赖 |
| --- | --- | --- | --- |
| NVIDIA | `nvidia-smi --query-gpu=...` | Linux / Windows | NVIDIA 驱动 |
| AMD | `/sys/class/drm/card*/device/gpu_busy_percent`、`mem_info_vram_*`、`hwmon` | Linux | amdgpu 驱动 |
| Intel | `intel_gpu_top -J` | Linux | `intel-gpu-tools` + perf 权限 |

- AMD 直接读 sysfs，不需要安装 `rocm-smi`；能读到显存、温度和功耗时会一并显示。
- Intel 核显没有独立显存，面板只显示利用率和频率（有功耗数据时也会显示）。
- Windows 下目前只有 NVIDIA 走 `nvidia-smi`，AMD / Intel 建议部署在 Linux 服务器上。
- 三种接口都探测不到时：`gpu_enabled` 强制为 `false`，模块隐藏，管理页显示「不支持」并禁用开关；直接调用接口强行开启会返回 `409`。
- 多卡时面板显示利用率最高的一张，表盘取各卡最大利用率；同时存在多厂商时按 NVIDIA → AMD → Intel 的顺序取第一组可用设备。
- 服务器热插显卡、补装驱动或 `intel-gpu-tools` 后，在管理页点「重新检测」即可，无需重启服务。

## Minecraft 服务器监控

默认关闭。管理员在「管理与设置 → Minecraft 服务器」打开后，顶栏左侧出现导航按钮，侧边栏内出现「MC 服务器」入口；关闭时按钮与侧边栏一并隐藏。协议为 Java 版 Server List Ping（SLP）。

- **添加服务器**：名称 + 主地址（`host` 或 `host:port`，默认 25565），RCON 暂未实现，标注 `Coming soon...`。
- **SRV 适配**：地址不带端口时先查询 `_minecraft._tcp.<地址>` 的 SRV 记录（例如 `xmls.wanandawang.top`），按优先级 / 权重连接 SRV 目标与端口；地址中显式写了端口则直连，不再查询 SRV。详情页只展示管理员填写的原始地址。
- **编辑服务器**：管理员在详情页右上角点「编辑服务器」，可修改名称与主地址；已有在线历史会保留。
- **空状态**：访客看到「管理员还没有添加服务器」；管理员看到「还没有添加服务器」和添加按钮。
- **服务器列表**：只有 1 台时进入后自动打开详情；2 台及以上显示「当前有 X 个服务器」，默认使用网格卡片（移动端友好），可切换为列表视图；两种视图都包含名称、在线/离线、地址、延迟、版本、在线人数（n/m）、MOTD。
- **详情页三张卡片**：
  - 服务器信息：MC 版本、协议号、延迟、在线玩家、MOTD；
  - 在线玩家：玩家名 + 小头像。通过 Mojang API 判断是否正版，正版取 `mc-heads.net` 的皮肤头，离线或查询失败使用内置 Steve 头像；服务器开启 `hide-online-players` 时提示未公开列表；
  - 在线历史：按分钟记录在线人数峰值，支持 15 分钟 / 30 分钟 / 1 小时 / 6 小时 / 12 小时 / 24 小时 / 7 天，以及 `yyyymmddhhmmss` 到 `yyyymmddhhmmss` 的自定义范围。
- 状态由后台线程每 30 秒探测一次；历史写入 `data/mc_history.json`（保留 8 天），重启服务后仍可查看。服务器列表保存在 `data/mc_servers.json`。

## 安全建议

- 默认只监听 `127.0.0.1`，本机访问足够安全。
- 面板和 `/api/metrics` 默认对访客只读开放（只包含指标、主机名和面板设置，不包含密码或令牌）。如果不希望公开，请在反向代理层加访问控制（Basic Auth / IP 白名单）。
- 如果要跨机器访问，**请放在 HTTPS 反向代理之后**（Caddy / Nginx / Traefik），不要把明文 HTTP 直接暴露到公网。
- 会话 Cookie 为 `HttpOnly` + `SameSite=Lax`；反向代理传递 `X-Forwarded-Proto: https` 时会自动加上 `Secure`。
- 密码使用 PBKDF2-HMAC-SHA256（600,000 次迭代）保存，`data/config.json` 权限会尽量收紧到 `0600`。
- 登录失败 5 次后锁定该 IP 5 分钟。

## 配置与数据

```
data/
├── config.json       # 管理员哈希 + 面板设置（首次初始化后生成）
├── setup-token.txt   # 初始化令牌（初始化完成后自动删除）
├── mc_servers.json   # Minecraft 服务器列表
├── mc_history.json   # Minecraft 在线人数历史
├── branding/         # 网页图标 / 壁纸文件
└── branding.json     # 图标 / 壁纸元信息
```

`config.json` 结构示例：

```json
{
  "version": 1,
  "admin": {
    "username": "admin",
    "algo": "pbkdf2_sha256",
    "iterations": 600000,
    "salt": "…",
    "hash": "…"
  },
  "settings": {
    "refresh_seconds": 5,
    "theme": "auto",
    "gpu_enabled": false,
    "minecraft_enabled": false,
    "branding": {
      "title": "SysMon",
      "palette": "violet",
      "primary": "#6750a4",
      "wallpaper_opacity": 35,
      "surface_mode": "theme",
      "surface_color": "#f3edf7"
    },
    "modules": [
      { "id": "cpu", "visible": true, "chart": "gauge", "hardware": true },
      { "id": "memory", "visible": true, "chart": "wave", "hardware": true },
      { "id": "disk", "visible": true, "chart": "wave", "hardware": true },
      { "id": "gpu", "visible": false, "chart": "gauge", "hardware": false },
      { "id": "net", "visible": true, "chart": "wave", "hardware": true }
    ]
  }
}
```

修改 `config.json` 后需要重启服务。若文件损坏，服务会拒绝启动并提示具体路径：备份后删除该文件即可重新初始化（管理员密码也会一并重置）。

## 部署

### systemd

先确保运行用户对数据目录有写权限：

```bash
sudo mkdir -p /opt/sysmon/data
sudo chown -R sysmon:sysmon /opt/sysmon
```

```ini
[Unit]
Description=SysMon system monitor
After=network.target

[Service]
Type=simple
User=sysmon
WorkingDirectory=/opt/sysmon
ExecStart=/opt/sysmon/.venv/bin/python /opt/sysmon/app.py --host 127.0.0.1 --port 8088
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Nginx 反向代理

```nginx
location / {
    proxy_pass http://127.0.0.1:8088;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

### Windows

```powershell
py -m pip install psutil
py app.py --port 8088
```

长期运行可以用「任务计划程序」或 [NSSM](https://nssm.cc/) 注册为服务。

## 目录结构

```
sysmon/
├── app.py                 # 入口：参数解析、启动服务
├── sysmon.sh              # SSH 后台启动 / 停止脚本
├── core/
│   ├── auth.py            # 密码哈希、会话、限流、初始化令牌
│   ├── branding.py        # 图标 / 壁纸文件与元信息
│   ├── collectors.py      # psutil + 多厂商 GPU 采集与环形历史
│   ├── config.py          # 配置模型、校验、原子写入
│   ├── hardware.py        # CPU / 内存条 / 磁盘 / 网卡型号
│   ├── minecraft.py       # Minecraft SLP、服务器列表、在线历史、头像
│   └── web.py             # HTTP 路由、API、安全响应头
├── static/
│   ├── index.html         # 登录 / 仪表盘 / 管理 / MC 视图
│   ├── app.css            # Material 3 设计令牌与组件
│   ├── app.js             # 轮询、Canvas 波形、SVG 表盘、品牌与配色
│   └── steve.png          # 离线玩家默认头像
├── tests/
│   └── test_smoke.py      # 冒烟测试
├── data/                  # 运行时生成（config.json、branding/、mc_*.json）
├── CHANGELOG.md
├── requirements.txt
└── README.md
```

## HTTP 接口

只读接口（`/api/state`、`/api/metrics`、`/branding/*`、`/api/mc/*`、`/healthz` 和静态文件）允许匿名访问；修改类接口需要管理员会话。MC 监控关闭时 `/api/mc/*` 统一返回 404。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/state` | 公开 | 登录状态 + 设置、指标、主机信息 |
| GET | `/api/metrics` | 公开 | 当前指标 + 最近 2 分钟历史 + 设置 |
| POST | `/api/setup` | 公开 + 初始化令牌 | 首次初始化 |
| POST | `/api/login` | 公开 | 登录 |
| POST | `/api/logout` | 公开 | 退出当前会话 |
| POST | `/api/config` | 管理员 | 保存面板设置（含标题、配色、MC 开关） |
| POST | `/api/gpu/rescan` | 管理员 | 重新检测 GPU |
| POST | `/api/password` | 管理员 | 修改密码 |
| GET | `/api/mc/servers` | 公开 | MC 服务器列表与状态 |
| POST | `/api/mc/servers` | 管理员 | 添加 MC 服务器 |
| GET | `/api/mc/servers/{id}` | 公开 | MC 服务器详情 |
| POST | `/api/mc/servers/{id}/update` | 管理员 | 编辑服务器名称 / 地址 |
| GET | `/api/mc/servers/{id}/history` | 公开 | 在线历史（`from_ts` / `to_ts` 秒级时间戳） |
| GET | `/api/mc/avatar/{name}` | 公开 | 玩家头像（正版皮肤或 Steve） |
| POST | `/api/branding/icon` | 管理员 | 上传图标（原始图片字节，≤ 512 KB） |
| POST | `/api/branding/wallpaper` | 管理员 | 上传壁纸（原始图片字节，≤ 8 MB） |
| POST | `/api/branding/icon/remove` | 管理员 | 恢复默认图标 |
| POST | `/api/branding/wallpaper/remove` | 管理员 | 移除壁纸 |
| GET | `/branding/icon` | 公开 | 读取当前图标 |
| GET | `/branding/wallpaper` | 公开 | 读取当前壁纸 |
| GET | `/healthz` | 公开 | 健康检查 |

除品牌资源上传使用 `image/*` 原始请求体外，其余 POST 接口要求 `Content-Type: application/json`，并校验 `Origin` 与 `Host` 一致。

## 测试

仓库自带一个冒烟测试脚本，会启动临时实例（随机端口 / 临时数据目录），走通初始化、登录、指标读取、设置保存（含标题）、访客只读与鉴权拒绝：

```bash
python tests/test_smoke.py
```

脚本只依赖标准库与 psutil，结束后自动清理，不会影响正在运行的实例。

## 常见问题

**启动报 `缺少依赖 psutil`**

执行 `python -m pip install -r requirements.txt`，或确认虚拟环境已激活。

**管理页 GPU 显示「不支持」**

没有探测到可用的采集接口。NVIDIA 确认 `nvidia-smi` 可直接运行；AMD 确认 `amdgpu` 驱动已加载且 `/sys/class/drm/card*/device/gpu_busy_percent` 可读；Intel 需要安装 `intel-gpu-tools` 并具备 perf 权限。装好后点「重新检测」。

**端口被占用**
`python app.py --port 8090` 换一个端口，或先结束占用 8088 的进程。

**忘记密码**
`python app.py --reset-password`，按提示输入新密码，然后重启服务。

**页面一直显示「重连中」**
检查服务进程是否存活、反向代理是否可达；浏览器控制台会显示具体请求错误。

**图标 / 壁纸替换后没变化**
浏览器会缓存 favicon。图标 URL 带时间戳版本，但仍可强制刷新（Ctrl+F5）或重新打开标签页确认。

**硬件信息只显示型号或容量**
细节依赖系统工具：Windows 需要 PowerShell CIM（普通用户即可），Linux 内存条信息需要 `dmidecode` 且通常要 root，网卡型号在部分虚拟化环境不可得。属正常退化，不影响监控数值。

**MC 服务器一直显示离线**
只支持 Java 版，需要服务器开启 TCP 端口且能被本机访问。地址填 `host` 或 `host:port`；默认端口 25565。地址不带端口时会尝试 SRV 查询，若服务器只有 SRV 记录也能连接；显式写了端口则直连。

**MC 玩家头像全是 Steve**
正版判断依赖 `api.mojang.com`，皮肤头依赖 `mc-heads.net`；服务器无法访问外网时会全部回退到内置 Steve，不影响在线人数与历史。

## 已知限制

- 容器环境：CPU / 内存指标来自宿主机视角（psutil），不读取 cgroup 限额；磁盘拿不到分区列表时会退化为根目录占用。
- Linux 磁盘统计会跳过 snap 的 `squashfs`/`loop` 镜像、Docker `overlay`、`tmpfs` 等虚拟分区，避免只读镜像被误算成 100%。
- GPU：Windows 下只有 NVIDIA 可用；AMD / Intel 依赖 Linux 的 sysfs 或 `intel-gpu-top`。Intel 需要 perf 权限，普通用户可能读不到。
- 多显卡时面板显示利用率最高的一张，不逐卡展开。
- 自定义配色由主色按算法派生容器色与文字色，与 Material You 官方动态取色的结果不完全一致；5 套预设观感更稳定。
- 自定义卡片颜色会同时决定卡片、顶栏与页面底色的色调，文字颜色按对比度自动切换；极端明暗颜色下个别边框的对比度可能不完美。
- 图标 / 壁纸存放在数据目录，会占用磁盘空间（壁纸上限 8 MB）。
- MC 仅支持 Java 版；SRV 查询依赖系统 DNS，显式写了端口的地址不会查询 SRV；RCON 尚未实现（界面标注 Coming soon）；在线历史按分钟峰值记录，采样间隔 30 秒。
- 会话保存在进程内存中，服务重启后需要重新登录。

## 扩展一个新模块

1. 在 `core/collectors.py` 的 `_sample` 中补充采集字段，并加入 `SERIES` 历史。
2. 在 `core/config.py` 的 `MODULES` 中登记 id、名称、可用的图表类型。
3. 在 `static/app.js` 的 `ICONS`、`buildCard`、`updateCard` 中增加对应渲染分支。

模块顺序、显示开关、图表类型都由配置驱动，无需改动路由。
