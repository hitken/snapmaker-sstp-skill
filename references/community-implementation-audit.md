# 社区实现与官方桌面端源码的差异审计

社区/论坛流传的 Snapmaker HTTP API 文档（如论坛帖、第三方 skill 仓库）**端点名和字段名经常是错的**。
以官方桌面端 Luban 的打包源码为准，逐条实测核对。

## 核对方法
1. 从桌面端打包代码取真实端点与表单字段名：
   ```bash
   grep -oE 'host\\+"/api/v1/[a-z_]+"' "/Applications/Snapmaker Luban.app/Contents/Resources/app/src/server/index.js"
   grep -oE '\\.send\\("[a-zA-Z]+="' "/Applications/Snapmaker Luban.app/Contents/Resources/app/src/server/index.js"
   ```
   （源码是压缩过的，但字面量完整保留，grep 得出所有端点与字段名。）
2. 用裸 socket 发请求实测，确认返回码和响应体。

## 端点名差异（最致命的一类）
| 社区文档写的 | 固件真实端点 | 错误端点实测结果 |
|---|---|---|
| `POST /api/v1/pause` | `POST /api/v1/pause_print` | 404 `The resource [/api/v1/pause] is not found.` |
| `POST /api/v1/resume` | `POST /api/v1/resume_print` | 404 |
| `POST /api/v1/stop` | `POST /api/v1/stop_print` | 404 |

**暂停/继续/停止三个核心功能如果照抄社区文档，三个都不可用。**

## status 响应字段名差异
真实存在的字段（实测完整列表）：
`status, x, y, z, homed, offsetX, offsetY, offsetZ, toolHead, nozzleTemperature,
nozzleTargetTemperature, heatedBedTemperature, heatedBedTargetTemperature,
isFilamentOut, workSpeed, printStatus, fileName, totalLines, estimatedTime,
currentLine, progress, elapsedTime, remainingTime, moduleList`

社区文档里常被写成 `nozzleTemperature1` / `nozzleTargetTemperature1`（不存在）
和 `isEnclosureDoorOpen`（不存在，固件此机型没有该字段）。

用错字段名不会报错，而是**静默不显示**——例如温度格式化函数判断 `nozzleTemperature1 is not None`，
永远为 False，输出里直接少一行温度，看起来像"打印机没数据"。

## 社区文档常见缺失项（官方源码有、社区没提）
- `POST /api/v1/execute_code`（字段 `code`）——可直接下发 G-code
- `override_nozzle_temperature` / `override_bed_temperature` / `override_z_offset` /
  `override_work_speed` / `override_laser_power`
- `filament_load` / `filament_unload` / `switch_extruder`（字段 `active`）
- `enclosure`（字段 `led`/`fan`/`isDoorEnabled`/`switch`）、`air_purifier_switch`、`air_purifier_fan_speed`
- `GET /api/v1/module_list`、`GET /api/v1/module_info`
- `GET /api/get_camera_image`（社区文档常断言"没有摄像头接口"，不成立）

## 社区实现本身的工程缺陷
以第三方 skill 仓库 `odrobnik/snapmaker-skill` 为例（OpenClaw skills 集合）：
- SKILL.md 声称支持 `--yes` / `--force` 安全开关，**代码里根本没实现**
  （实跑 `stop --yes` → `error: unrecognized arguments: --yes`）
- 依赖 `requests`；在 Python 3.14 环境下 urllib3 报 `TypeError: 'type' object is not subscriptable`，
  直接崩。零依赖 stdlib 实现更稳
- `cmd_discover` 里 `if probe_ip:` 缩进错位，无 config 时会 `NameError`
- 配置路径绑定 OpenClaw 的 workspace/`skills/` 目录结构，换宿主要重写

## 结论与做法
- 社区实现的**工程外壳**（沙箱限制、文件类型白名单、打印中保护、破坏性操作确认、事件监控）
  值得借鉴
- 社区实现的**协议知识**必须逐条对着桌面端源码 + 实测校验后再用
- 自己的实现优先用官方桌面端源码作为协议真值来源
