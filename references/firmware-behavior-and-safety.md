# 固件行为与安全模型

## 状态字段的真实语义

固件里 `status` 与 `printStatus` 是两套独立标志，**可能不一致**：

| 字段 | 取值 | 含义 |
|---|---|---|
| `status` | `IDLE` / `RUNNING` / `PAUSED` | 连接/任务主状态 |
| `printStatus` | `Idle` / `Printing` / `Paused` / … | 打印任务子状态 |

实测踩到的坑：在空闲时调用过一次 `pause_print` 之后，`printStatus` 永久卡在 `Paused`，
而 `status` 仍是 `IDLE`、`progress` 是 0、目标温度是 0、喷嘴没加热。
此时 `resume_print` / `stop_print` / `start_print` **全部返回 409**，无法复位，
只能重启打印机清除。

**结论：判断"是否真在打印"必须同时看 `progress`、`fileName` 和目标温度，
不能只看单个布尔字段。** 脚本里的 `is_busy()` 就是这个逻辑：

```python
if s.get("status") in ("RUNNING", "PAUSED"): return True
if (s.get("progress") or 0) > 0: return True
if (s.get("nozzleTargetTemperature") or 0) > 0: return True
```

`remainingTime` 也是残留估算值，空闲时会显示几小时的荒谬数字，不要当真实剩余时间用。

## 探测端点时不要调有副作用的接口

枚举/验证 API 时只调只读端点：`status` / `module_list` / `module_info` / `print_file` / `enclosure`。

有副作用、且**在空闲时也会改写固件状态**的：
`pause_print` / `stop_print` / `start_print` / `prepare_print`（会写 `fileName`）/ `execute_code`。

`prepare_print` 会把 `fileName` 设成上传的文件名（实测 `reset_probe.gcode`），
但不会真的开始打印。

## 独占槽位

`/api/v1/connect` 同时只允许一个客户端。触摸屏面板和 Luban 桌面端抢同一个槽位，
冲突时返回 403 `someone trying to connect to Touchscreen`。

**`/api/v1/disconnect` 会把 Luban 桌面端踢下线**（桌面端日志出现
`wifi connection offline: msg=Unauthorized`），恢复办法是让桌面端自己重新 connect。
不要用脚本"接管"连接槽位。

## 脚本内置的安全模型

| 防护 | 实现 |
|---|---|
| 上传文件类型白名单 | `.gcode/.gc/.nc/.cnc/.stl/.3mf/.snap3dp/.snapcnc/.snaplaser` |
| 上传目录沙箱 | 默认只允许 cwd、`/tmp`、`$TMPDIR`；其他目录用 `--allow-dir` 显式放行 |
| 打印中保护 | `send` 检测到在打印则拒绝 |
| 破坏性操作确认 | `start` / `stop` / `gcode` / `nozzle>0` / `bed>0` / `send --start` 需交互确认，`--yes` 跳过 |
| 零第三方依赖 | 纯 stdlib `socket`，不需要 `requests`（3.14 环境下 `requests`+`urllib3` 会崩） |
| 不改用户配置 | token 只读 `machine.json` 的 `state.server.token` |

token 读取路径注意：Luban 4.x 是 `state.server.token`，
**不是** `server.token`（早期版本/变体才在后者）。脚本两处都找。

## 网络安全

8080 端口是**纯 HTTP、无加密**，token 明文存放在
`~/Library/Application Support/snapmaker-luban/machine.json`。
同网段任何主机都能直接下发 G-code 控制机器、加热、甚至打印。

部署在办公网时值得跟 IT 确认 VLAN 隔离或防火墙规则。

## 摄像头

`GET /api/get_camera_image?index=N&token=...` 存在（社区文档常断言"没有摄像头接口"，不成立）。
注意它在 `/api/` 前缀下，**不在 `/api/v1/` 下**。
