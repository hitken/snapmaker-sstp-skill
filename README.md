# Snapmaker SSTP — LAN 控制 Snapmaker 2.0 打印机

通过 **SSTP over HTTP** 在局域网内直接控制 Snapmaker 2.0 打印机（A250 等），
不需要官方桌面端在场，也不需要 mDNS/OctoPrint。

> **协议真值来源是官方桌面端 Luban 的打包源码**，不是社区文档。
> 社区文档的端点名和字段名经常是错的，而且**错了不报错、静默失效**。
> 详见 [`references/community-implementation-audit.md`](references/community-implementation-audit.md)。

## 特性

- **零依赖**：纯 Python stdlib（`socket` / `json`），不需要 `requests`
  （Python 3.14 环境下 `requests` + `urllib3` 会直接崩）
- **端点全覆盖**：状态 / 模块 / 摄像头 / G-code / 温度 / 速度 / Z offset /
  激光功率 / 换料 / 换挤出机 / 封闭舱 / 空气净化器 / 打印任务全生命周期
- **内置安全模型**：文件类型白名单 + 目录沙箱 + 打印中保护 + 破坏性操作确认
- **事件监控**：`watch --events` 输出完成/缺料/暂停/里程碑事件
- **自动发现**：UDP 广播（端口 20054）—— 这才是实际生效的发现机制，mDNS 那个基本不生效

## 快速开始

```bash
git clone https://github.com/hitken/snapmaker-sstp-skill.git
cd snapmaker-sstp-skill

# 发现打印机（UDP 广播，同网段有效）
python3 scripts/snapmaker_sstp.py discover

# 看状态（token 自动从 Luban 的 machine.json 读，只读）
python3 scripts/snapmaker_sstp.py --host 172.16.38.160 status
```

输出：

```
状态     : IDLE / Idle
坐标     : X=-15.5 Y=0 Z=229.301  已归零=True
喷嘴温度 : 29°C / 目标 0°C
热床温度 : 30°C / 目标 0°C
打印速度 : 1500
耗材     : 正常
打印头   : TOOLHEAD_3DPRINTING_1
```

## 命令

| 命令 | 说明 |
|---|---|
| `discover` | UDP 广播发现打印机 |
| `status` / `status-json` | 当前状态（人类可读 / JSON） |
| `modules` | 模块列表与模块信息 |
| `enclosure` | 封闭舱状态（门/LED/风扇） |
| `camera` | 抓摄像头图像存到本地 |
| `print-file` | 导出打印机上的文件 |
| `gcode --code "M115"` | 下发 G-code |
| `nozzle --value 200` / `bed --value 60` | 设置温度 |
| `speed --value 1500` | 设置打印速度 |
| `z-offset --value 0.05` | 设置 Z offset |
| `laser --value 50` | 设置激光功率 |
| `filament load` / `filament unload` | 装料 / 卸料 |
| `extruder 0` | 切换挤出机 |
| `send file.gcode [--start]` | 上传作业文件（只准备不启动） |
| `start` / `pause` / `resume` / `stop` | 打印任务控制 |
| `watch [--interval 5] [--events]` | 轮询进度 / 只输出事件 |

全局选项：`--host` `--token` `--port` `--config` `--yes` `--allow-dir`

### 配置文件

```json
{ "host": "172.16.38.160", "token": "<token>", "port": 8080 }
```

```bash
python3 scripts/snapmaker_sstp.py --config printer.json status
```

## 三个必须知道的坑

**1. 端点名不能照抄社区文档**

| 社区文档 | 正确 |
|---|---|
| `POST /api/v1/pause` | `POST /api/v1/pause_print` |
| `POST /api/v1/resume` | `POST /api/v1/resume_print` |
| `POST /api/v1/stop` | `POST /api/v1/stop_print` |

用错就是 404。表单字段名同理：`workSpeed` 不是 `value`，
`nozzleTemp` 不是 `nozzleTemperature`。

**2. `status` 和 `printStatus` 可能不一致**

在空闲时误调过一次 `pause_print`，固件的 `printStatus` 会**永久卡在 `Paused`**，
而 `status` 仍是 `IDLE`。此时 resume/stop/start 全部 409，无法复位，只能重启打印机。
判断"是否真在打印"要同时看 `progress` / `fileName` / 目标温度。

**3. `/api/v1/connect` 是独占槽位**

调 `/api/v1/disconnect` 会把 Luban 桌面端踢下线。不要用脚本"接管"连接。

## 安全

8080 是纯 HTTP 无加密，token 明文在 `machine.json`，同网段任何主机都能下发 G-code。
办公网部署请跟 IT 确认 VLAN 隔离。

脚本侧：上传文件类型白名单 + 目录沙箱（默认只有 cwd / `/tmp` / `$TMPDIR`）+
打印中拒绝上传 + 破坏性操作需确认。

## 协议速查

Base：`http://<ip>:8080/api/v1/<endpoint>?token=<token>`
（摄像头例外，在 `/api/get_camera_image`）

| 分类 | 端点 | 表单字段 |
|---|---|---|
| 连接 | `connect` `disconnect` `status` | — |
| 打印 | `prepare_print` `start_print` `pause_print` `resume_print` `stop_print` `upload` | — |
| G-code | `execute_code` | `code` |
| 参数 | `override_nozzle_temperature` | `nozzleTemp` |
| | `override_bed_temperature` | `heatedBedTemp` |
| | `override_z_offset` | `zOffset` |
| | `override_work_speed` | `workSpeed` |
| | `override_laser_power` | `laserPower` |
| 物料 | `filament_load` `filament_unload` | — |
| | `switch_extruder` | `active` |
| 外设 | `enclosure` | `led` / `fan` / `isDoorEnabled` / `switch` |
| | `air_purifier_switch` `air_purifier_fan_speed` | — |
| 查询 | `module_list` `module_info` `active_extruder` `print_file` | — |

## License

MIT
