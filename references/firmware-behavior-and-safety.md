# 固件行为与安全模型

## 状态字段的真实语义

固件里 `status` 与 `printStatus` 是两套独立标志，**可能不一致**：

| 字段 | 取值 | 谁在用 |
|---|---|---|
| `status` | `IDLE` / `RUNNING` / `PAUSED` | **桌面端 UI 主状态**（`status.toLowerCase()`） |
| `printStatus` | `Idle` / `Printing` / `Paused` / … | 仅打印信息块，空闲时会被丢弃 |

### 空闲时 `printStatus` 变成 `Paused` 是正常的，不是故障
在空闲状态下调过一次 `pause_print` 后，固件 `/api/v1/status` 的 `printStatus`
会变成 `Paused` 并**一直保持**，而 `status` 仍是 `IDLE`、`progress`/`currentLine`/
`totalLines` 全是 0、目标温度 0。`resume_print` / `stop_print` / `start_print`
此时都返回 409，无法复位。

**但这不影响任何实际使用**——桌面端源码 `getGcodePrintingInfo` 有守卫：

```js
if (!currentLine || !estimatedTime || !totalLines) return {};
```

`currentLine`/`estimatedTime`/`totalLines` 任一为 0（空闲时必然如此），
整个打印信息块直接返回空对象，`printStatus` 和 `fileName` 都被丢弃。
UI 主状态用的是 `status.toLowerCase()`（`idle`），跟 `printStatus` 无关。

**所以：桌面端显示正常就是正常，不需要重启打印机。**
判断"是否真在打印"要看 `status` / `progress` / `currentLine` / 目标温度，
`printStatus` 在空闲时是不可信字段。

`remainingTime` 也是残留估算值，空闲时会显示几小时的荒谬数字，不要当真实剩余时间用。

### fileName 残留同理
`prepare_print` 会把 `fileName` 设成上传的文件名，即使从没启动过。
守卫条件让它不出现在桌面端 UI 上，但 `/api/v1/status` 里能看到。
属于测试痕迹，非故障。

## 探测端点时不要调有副作用的接口

枚举/验证 API 时只调只读端点：`status` / `module_list` / `module_info` / `print_file` / `enclosure`。

有副作用、且**在空闲时也会改写固件状态**的：
`pause_print` / `stop_print` / `start_print` / `prepare_print`（会写 `fileName`）/ `execute_code`。

`prepare_print` 会把 `fileName` 设成上传的文件名（实测 `reset_probe.gcode`），
但不会真的开始打印。

不过如上所述，这些残留字段在空闲时都不会影响桌面端 UI 或实际功能。

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
