---
name: snapmaker-sstp
description: Control a Snapmaker printer over LAN via SSTP protocol.
version: 1.0.0
author: guoyitong
license: MIT
metadata:
  hermes:
    tags: [3d-printer, snapmaker, sstp, hardware, lan]
    related_skills: []
---

# Snapmaker 打印机：LAN 控制协议（SSTP over HTTP）

## 协议真值来源（铁律）
**以官方桌面端 Luban 的打包源码为准，不要照抄社区/论坛文档。**
社区文档的端点名和字段名经常是错的，且错了**不会报错而是静默失效**
（温度字段写错 → 输出里直接少一行；端点名写错 → 404）。

核对方法与逐条差异见 `references/community-implementation-audit.md`。

端点与字段名的提取命令：
```bash
grep -oE 'host\\+"/api/v1/[a-z_]+"' "/Applications/Snapmaker Luban.app/Contents/Resources/app/src/server/index.js"
grep -oE '\\.send\\("[a-zA-Z]+="' "/Applications/Snapmaker Luban.app/Contents/Resources/app/src/server/index.js"
```

## When to Use
- 需要在无 GUI 情况下控制 Snapmaker 打印机（上传 G-code、启停任务、读状态）
- 排查“Snapmaker Luban 桌面端发现不到设备 / 连接失败”
- 需要判断能否用 OctoPrint/Cura 直连打印机
- 拿到第三方的 Snapmaker API 文档/skill，要校验其端点名与字段名是否正确

## 配套文件
- `scripts/snapmaker_sstp.py` — 零依赖 stdlib CLI。命令：
  `discover / status / status-json / modules / enclosure / camera / print-file /
  gcode / nozzle / bed / speed / z-offset / laser / filament / extruder /
  send / start / pause / resume / stop / watch`
  自带只读 token 读取（从 Luban machine.json 的 `state.server.token`），**不修改**任何用户配置。
  内置安全模型：文件类型白名单 + 目录沙箱 + 打印中保护 + 破坏性操作确认（`--yes` 跳过）。
- `references/community-implementation-audit.md` — 社区实现与官方源码的逐条差异审计
- `references/firmware-behavior-and-safety.md` — 固件状态字段真实语义、安全模型、网络暴露面
- `README.md` — 面向 GitHub 读者的快速上手

## 快速上手
```bash
python3 scripts/snapmaker_sstp.py discover
python3 scripts/snapmaker_sstp.py --host <ip> status
python3 scripts/snapmaker_sstp.py --host <ip> watch --events
```
token 缺省从 Luban `machine.json` 的 `state.server.token` 读（只读）。
配置可用 `--config printer.json` 传 `{host, token, port}`。

逆向来源：`/Applications/Snapmaker Luban.app/Contents/Resources/app/src/server/index.js`
（Electron 应用，主进程是打包后的 server 代码，字符串可 grep）。

## 安全模型（脚本内置）
- 上传文件类型白名单：`.gcode/.gc/.nc/.cnc/.stl/.3mf/.snap3dp/.snapcnc/.snaplaser`
- 上传目录沙箱：默认只允许 cwd、`/tmp`、`$TMPDIR`；其他目录用 `--allow-dir` 显式放行
- 打印中拒绝上传（判据：`status`/`progress`/目标温度，**不只看单个字段**）
- 破坏性操作需交互确认：`start`/`stop`/`gcode`/`nozzle>0`/`bed>0`/`send --start`

## 设备识别
- mDNS/UDP 广播回复格式：`名称@IP|key:value|...`，例如
  `Snapmaker-hitken@172.16.38.160|model:Snapmaker 2 Model A250|status:IDLE`
- 出现 `SACP:` 字段表示该机支持 SACP 协议分支。

## 发现机制（两个并行 finder，只有一个真正生效）
1. **UDP 广播（实际生效的那个）**
   - 端口 `20054`（源码常量 `kd=20054`）
   - 客户端向本机网段**广播地址**发 8 字节明文 `discover`
   - 机器回一条 UDP 报文；`|` 分隔字段解析为 name/address/model/status
   - 源码：`bd` finder → `sendDiscoverMessage`
2. **mDNS（基本不生效）**
   - 查询 `_printer._tcp.local` + `_printer._udp.local` 的 PTR
   - **但代码只匹配 `"PTR"===e.type && "_printer._udp.local"===e.name`**，
     所以只有 `_printer._udp` 响应会被采纳，`_printer._tcp`（普通 IPP/扫描类打印机）被直接丢弃
   - 源码：`ld` finder

排查"发现不到设备"时，先用 UDP 广播验证，不要只信 `dns-sd -B _printer._tcp`。

## 协议探测顺序（ConnectionManager）
SACP-over-TCP → SACP-over-UDP → HTTP。三种全失败才报错。
日志行 `Detected protocol: HTTP` 表示走第三种分支。

## 业务接口
Base：`http://<ip>:8080`，服务器头 `Server: AndServer/2.0.0`（Android 内嵌 HTTP 框架）。
所有请求带 `?token=<token>`，token 明文存在
`~/Library/Application Support/snapmaker-luban/machine.json` 的 `server.token`。

| 分类 | 端点 | 表单字段 |
|---|---|---|
| 连接 | `POST /api/v1/connect`、`POST /api/v1/disconnect`、`GET /api/v1/status` | `token` |
| 打印 | `POST /api/v1/prepare_print`（multipart，带 `type`=3DP/Laser/CNC + `file`）、`/api/v1/start_print`、`/api/v1/pause_print`、`/api/v1/resume_print`、`/api/v1/stop_print`、`/api/v1/upload` | `token` |
| G-code | `POST /api/v1/execute_code` | `token`、`code` |
| 参数覆盖 | `/api/v1/override_nozzle_temperature` | `token`、`nozzleTemp` |
| | `/api/v1/override_bed_temperature` | `token`、`heatedBedTemp` |
| | `/api/v1/override_z_offset` | `token`、`zOffset` |
| | `/api/v1/override_work_speed` | `token`、`workSpeed` |
| | `/api/v1/override_laser_power` | `token`、`laserPower` |
| 物料 | `/api/v1/filament_load`、`/api/v1/filament_unload` | `token` |
| | `/api/v1/switch_extruder` | `token`、`active`（挤出机索引） |
| 外设 | `/api/v1/enclosure` | `token` + `led` / `fan` / `isDoorEnabled` / `switch` |
| | `/api/v1/air_purifier_switch`、`/api/v1/air_purifier_fan_speed` | `token` |
| 只读查询 | `GET /api/v1/module_list`、`/api/v1/module_info`、`/api/v1/active_extruder`、`/api/v1/print_file`、`/api/get_camera_image` | `token` |

**字段名必须用源码里的原名**（`workSpeed` 不是 `value`，`nozzleTemp` 不是 `nozzleTemperature`），
用错字段名会得到 400 Bad Request。

已实测：
- `/api/v1/module_list` → `{"moduleList":[{"key":1,"moduleId":0,"status":true},{"key":2,"moduleId":512,"status":true}]}`
  （key=1 喷嘴，key=2 热床）
- `/api/v1/module_info` → 每个模块的温度/耗材状态
- `/api/v1/active_extruder` → 409 `Wrong head type.`（当前 toolHead 是 3DP 打印头）
- `/api/v1/override_work_speed` 传 `workSpeed=1500` → 200 OK

`GET /api/v1/status` 响应里也带 `moduleList` 字段（布尔形式），两者格式不同，别混用。

### status 响应字段名（实测完整列表）
`status, x, y, z, homed, offsetX, offsetY, offsetZ, toolHead,
nozzleTemperature, nozzleTargetTemperature, heatedBedTemperature,
heatedBedTargetTemperature, isFilamentOut, workSpeed, printStatus, fileName,
totalLines, estimatedTime, currentLine, progress, elapsedTime, remainingTime, moduleList`

**没有** `nozzleTemperature1` / `nozzleTargetTemperature1` / `isEnclosureDoorOpen`。
用错字段名不报错，只是静默不显示——看起来像“打印机没数据”。

## 独占槽位（关键坑）
`/api/v1/connect` 同一时刻只允许一个客户端。触摸屏面板和桌面端抢同一个槽位，
冲突时返回 403 `someone trying to connect to Touchscreen`。
**调试时误调 `/api/v1/disconnect` 会把桌面端踢下线**（日志
`wifi connection offline: msg=Unauthorized`），恢复办法是让桌面端重新 connect，
不要试图用脚本"接管"。

## OctoPrint 兼容层（能力远小于预期）
桌面端在 `0.0.0.0:5000` 起代理，**只实现 3 个路由**：
| 路由 | 行为 |
|---|---|
| `GET /` | `Welcome to Snapmker OctoPrint proxy service`（原文拼写错误） |
| `GET /api/version` | 固定返回 `{"api":"0.1","server":"1.2.3","text":"OctoPrint 1.2.3/Dummy"}` |
| `POST /api/files/local` | multipart 上传 G-code → 转 SSTP upload 发给机器 |

`/api/state`、`/api/printer`、`/api/files`、`/api/settings`、`/api/job` **全部 404**。
日志里的 `Request GET /api/state completed in 0ms` 只表示请求处理完，**404 也算 completed**，
别据此以为接口存在。

结论：OctoPrint 层只解决"上传"，**监控和任务控制必须直连 SSTP**。

## 探测端点时不要调有副作用的接口
枚举/验证 API 时，只调**只读**端点（status / module_list / module_info / print_file）。
`pause_print` / `stop_print` / `start_print` 即使在空闲时也会**改写固件状态**。

### 空闲时的 `printStatus` 是噪声，不代表故障
在空闲状态下调过一次 `pause_print` 后，固件 `/api/v1/status` 的 `printStatus` 会变成
`Paused` 并**一直保持**，而 `status` 仍是 `IDLE`、`progress`/`currentLine`/`totalLines`
全是 0、目标温度 0。看起来像"卡住了"，**其实没有任何影响**——

原因在桌面端源码 `getGcodePrintingInfo` 的守卫：
```js
if (!currentLine || !estimatedTime || !totalLines) return {};
```
`currentLine`/`estimatedTime`/`totalLines` 任一为 0（空闲时必然如此），
整个打印信息块直接返回空对象，`printStatus` 和 `fileName` 都被丢弃。
UI 主状态用的是 `status.toLowerCase()`（即 `idle`），与 `printStatus` 无关。

**结论：桌面端显示正常就是正常，不需要重启打印机。**
`printStatus` 在空闲时只应作为"不可信字段"看待，判断是否真在打印要看
`progress` / `currentLine` / 目标温度 / `status`。

### fileName 残留同理
`prepare_print` 会把 `fileName` 设成上传的文件名，即使从没启动过。
守卫条件同样让它不出现在桌面端 UI 上，但 `/api/v1/status` 里能看到。
属于测试痕迹，非故障。

## 安全
8080 是纯 HTTP 无加密，token 明文在 machine.json，同网段任何主机可下发 G-code。
部署在办公网时值得跟 IT 确认 VLAN 隔离。

## Luban token 读取路径
`~/Library/Application Support/snapmaker-luban/machine.json` 里 token 在
**`state.server.token`**（不是 `server.token`；`state.session.token` 是另一个 JWT，无效）。
读错路径会报"缺少 token"。脚本两处都试。
```python
import socket, json
HOST, PORT, TOKEN = "172.16.38.160", 8080, "<token>"

def hit(path, method="GET", body=None, ct="application/x-www-form-urlencoded"):
    s = socket.create_connection((HOST, PORT), timeout=6); s.settimeout(8)
    head = f"{method} {path} HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n"
    if body is not None:
        head += f"Content-Type: {ct}\r\nContent-Length: {len(body)}\r\n"
    s.sendall((head + "\r\n").encode() + (body or b""))
    buf = b""
    while True:
        try: c = s.recv(8192)
        except Exception: break
        if not c: break
        buf += c
    s.close()
    return buf

status = json.loads(hit(f"/api/v1/status?token={TOKEN}").split(b"\r\n\r\n", 1)[1])
```
