#!/usr/bin/env python3
"""Snapmaker 2.0 (SSTP over HTTP) 控制 CLI — 零依赖，纯 stdlib。

端点与字段名全部依据官方桌面端 Luban 打包源码逆向并实测校验。
不要用社区文档里的端点名（pause/resume/stop 会 404，
正确是 pause_print/resume_print/stop_print）。

用法:
  # token 缺省从 Luban 的 machine.json 读（只读）
  python3 snapmaker_sstp.py --host <ip> status
  python3 snapmaker_sstp.py --host <ip> discover
  python3 snapmaker_sstp.py --host <ip> modules
  python3 snapmaker_sstp.py --host <ip> gcode --code "M115"
  python3 snapmaker_sstp.py --host <ip> speed --value 1500
  python3 snapmaker_sstp.py --host <ip> send file.gcode --start
  python3 snapmaker_sstp.py --host <ip> watch --interval 5
  python3 snapmaker_sstp.py --host <ip> watch --events

安全约定:
  - 上传文件限白名单扩展名
  - 上传文件必须在 --allow-dir（默认 cwd、/tmp、TMPDIR）之内
  - 打印中拒绝上传
  - 破坏性/产热操作（start/stop/加热）需确认，--yes 跳过
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path

DEFAULT_PORT = 8080
DISCOVER_PORT = 20054

# 允许上传的扩展名（对齐 Luban 支持的作业格式）
ALLOWED_EXTENSIONS = {
    ".gcode", ".gc", ".nc", ".cnc", ".stl", ".3mf",
    ".snap3dp", ".snapcnc", ".snaplaser",
}

# 会改变机器状态（加热、运动、任务）的命令 → 需确认
MUTATING_COMMANDS = {"start", "stop", "pause", "resume",
                      "nozzle", "bed", "gcode", "send", "home", "laser"}


# ---------- 底层 HTTP（裸 socket，避免 requests 依赖） ----------

def http(host: str, port: int, path: str, method: str = "GET",
         body: bytes = b"", content_type: str | None = None,
         timeout: int = 15) -> tuple[int, str, bytes]:
    """返回 (status_code, reason, body)。非 2xx 不抛异常，由调用方判断。"""
    head = f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n"
    if body:
        head += f"Content-Type: {content_type or 'application/x-www-form-urlencoded'}\r\n"
        head += f"Content-Length: {len(body)}\r\n"
    head += "\r\n"
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    try:
        s.sendall(head.encode() + body)
        buf = b""
        while True:
            try:
                chunk = s.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if b"\r\n\r\n" not in buf:
        return 0, "NO_RESPONSE", buf
    head_b, _, body_b = buf.partition(b"\r\n\r\n")
    first = head_b.split(b"\r\n")[0].decode("latin1")
    parts = first.split(" ", 2)
    try:
        code = int(parts[1])
    except (IndexError, ValueError):
        return 0, first, body_b
    reason = parts[2] if len(parts) > 2 else ""
    return code, reason, body_b


class Printer:
    def __init__(self, host: str, token: str, port: int = DEFAULT_PORT):
        self.host, self.token, self.port = host, token, port

    def _url(self, endpoint: str) -> str:
        return f"/api/v1/{endpoint}?token={self.token}"

    def _post_form(self, endpoint: str, fields: dict) -> tuple[int, str, bytes]:
        body = "&".join(f"{k}={v}" for k, v in fields.items()).encode()
        return http(self.host, self.port, self._url(endpoint), "POST", body)

    def _get(self, endpoint: str, timeout: int = 15) -> tuple[int, str, bytes]:
        return http(self.host, self.port, self._url(endpoint), "GET", timeout=timeout)

    # --- 只读 ---
    def status(self) -> dict:
        code, _, body = self._get("status")
        if code != 200:
            raise RuntimeError(f"status 失败 HTTP {code}: {body[:200]!r}")
        return json.loads(body)

    def modules(self) -> dict:
        code, _, body = self._get("module_list")
        if code != 200:
            raise RuntimeError(f"module_list 失败 HTTP {code}: {body[:200]!r}")
        return json.loads(body)

    def module_info(self) -> dict:
        code, _, body = self._get("module_info")
        if code != 200:
            raise RuntimeError(f"module_info 失败 HTTP {code}: {body[:200]!r}")
        return json.loads(body)

    def print_file(self) -> bytes:
        code, _, body = self._get("print_file", timeout=60)
        if code != 200:
            raise RuntimeError(f"print_file 失败 HTTP {code}: {body[:200]!r}")
        return body

    def camera_image(self, index: int = 0) -> bytes:
        """注意：这是 /api/ 前缀，不在 /api/v1 下。"""
        code, _, body = http(self.host, self.port,
                             f"/api/get_camera_image?index={index}&token={self.token}",
                             "GET", timeout=30)
        if code != 200:
            raise RuntimeError(f"camera 失败 HTTP {code}: {body[:200]!r}")
        return body

    # --- 连接槽位（独占，会把 Luban 桌面端挤下线） ---
    def connect(self) -> tuple[int, str, bytes]:
        return http(self.host, self.port, self._url("connect"), "POST", b"")

    def disconnect(self) -> tuple[int, str, bytes]:
        """注意：调用会把 Luban 桌面端踢下线（token 失效）。慎用。"""
        return http(self.host, self.port, self._url("disconnect"), "POST", b"")

    # --- 控制 ---
    def execute_code(self, code: str) -> tuple[int, str, bytes]:
        return self._post_form("execute_code", {"code": code})

    def set_work_speed(self, value: int) -> tuple[int, str, bytes]:
        return self._post_form("override_work_speed", {"workSpeed": value})

    def set_nozzle_temp(self, value: int) -> tuple[int, str, bytes]:
        return self._post_form("override_nozzle_temperature", {"nozzleTemp": value})

    def set_bed_temp(self, value: int) -> tuple[int, str, bytes]:
        return self._post_form("override_bed_temperature", {"heatedBedTemp": value})

    def set_z_offset(self, value: float) -> tuple[int, str, bytes]:
        return self._post_form("override_z_offset", {"zOffset": value})

    def set_laser_power(self, value: int) -> tuple[int, str, bytes]:
        return self._post_form("override_laser_power", {"laserPower": value})

    def switch_extruder(self, index: int) -> tuple[int, str, bytes]:
        return self._post_form("switch_extruder", {"active": index})

    def filament_load(self) -> tuple[int, str, bytes]:
        return self._post_form("filament_load", {})

    def filament_unload(self) -> tuple[int, str, bytes]:
        return self._post_form("filament_unload", {})

    def enclosure_set(self, **fields) -> tuple[int, str, bytes]:
        """led / fan / isDoorEnabled / switch"""
        return self._post_form("enclosure", fields)

    def enclosure_status(self) -> tuple[int, str, bytes]:
        return self._get("enclosure")

    def air_purifier_switch(self, on: bool) -> tuple[int, str, bytes]:
        return self._post_form("air_purifier_switch", {"switch": str(on).lower()})

    def air_purifier_fan_speed(self, value: int) -> tuple[int, str, bytes]:
        return self._post_form("air_purifier_fan_speed", {"speed": value})

    # --- 打印任务 ---
    def prepare_print(self, path: str, print_type: str = "3DP") -> tuple[int, str, bytes]:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(path)
        bnd = "----b" + uuid.uuid4().hex
        data = p.read_bytes()
        body = (
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"token\"\r\n\r\n{self.token}\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\n{print_type}\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{p.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{bnd}--\r\n".encode()
        ctype = f"multipart/form-data; boundary={bnd}"
        return http(self.host, self.port, self._url("prepare_print"), "POST", body, ctype, timeout=120)

    def start_print(self):
        return self._post_form("start_print", {})

    def pause_print(self):
        return self._post_form("pause_print", {})

    def resume_print(self):
        return self._post_form("resume_print", {})

    def stop_print(self):
        return self._post_form("stop_print", {})


# ---------- 发现 ----------

def discover(timeout: float = 3.0, retries: int = 2,
             target: str | None = None) -> list[dict]:
    """UDP 广播 discover（端口 20054）。同一网段有效；跨网段用 target 单播探测。"""
    found: dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        for _ in range(retries):
            if target:
                sock.sendto(b"discover", (target, DISCOVER_PORT))
            else:
                sock.sendto(b"discover", ("255.255.255.255", DISCOVER_PORT))
            try:
                while True:
                    data, addr = sock.recvfrom(4096)
                    reply = data.decode("utf-8", errors="replace").strip("'\"")
                    parts = reply.split("|")
                    info: dict = {}
                    info["ip"] = parts[0].split("@", 1)[1] if "@" in parts[0] else addr[0]
                    info["name"] = parts[0].split("@", 1)[0]
                    for part in parts[1:]:
                        if ":" in part:
                            k, v = part.split(":", 1)
                            info[k.strip().lower()] = v.strip()
                    found[info["ip"]] = info
            except socket.timeout:
                pass
            if found:
                break
    finally:
        sock.close()
    return list(found.values())


# ---------- 输出 ----------

def fmt_status(s: dict) -> str:
    lines = [
        f"状态     : {s.get('status')} / {s.get('printStatus')}",
        f"坐标     : X={s.get('x')} Y={s.get('y')} Z={s.get('z')}  已归零={s.get('homed')}",
        f"喷嘴温度 : {s.get('nozzleTemperature')}°C / 目标 {s.get('nozzleTargetTemperature')}°C",
        f"热床温度 : {s.get('heatedBedTemperature')}°C / 目标 {s.get('heatedBedTargetTemperature')}°C",
        f"打印速度 : {s.get('workSpeed')}",
        f"耗材     : {'缺料!' if s.get('isFilamentOut') else '正常'}",
        f"打印头   : {s.get('toolHead')}",
    ]
    if s.get("fileName"):
        lines.append(f"文件     : {s['fileName']}  进度 {s.get('progress', 0) * 100:.1f}%")
    return "\n".join(lines)


def is_busy(s: dict) -> bool:
    """判断是否真在打印。

    注意 status 与 printStatus 可能不一致（status=IDLE 但 printStatus=Paused），
    所以同时看进度与目标温度。
    """
    if s.get("status") in ("RUNNING", "PAUSED"):
        return True
    if (s.get("progress") or 0) > 0:
        return True
    if (s.get("nozzleTargetTemperature") or 0) > 0:
        return True
    return False


def fmt_time(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return "-"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


# ---------- 安全 ----------

def allowed_dirs(extra: list[str] | None) -> list[Path]:
    dirs = [Path.cwd(), Path("/tmp"), Path("/private/tmp")]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        dirs.append(Path(tmpdir))
    for d in extra or []:
        dirs.append(Path(d).expanduser())
    out = []
    for d in dirs:
        try:
            out.append(d.resolve())
        except OSError:
            pass
    return out


def check_file_allowed(path: str, extra_dirs: list[str] | None) -> Path:
    p = Path(path).expanduser()
    # 先看后缀（在 resolve 之前，避免解析失败掩盖真实原因）
    if p.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"不允许的文件类型 {p.suffix or '(无扩展名)'}；"
            f"支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(path)
    roots = allowed_dirs(extra_dirs)
    if not any(p == r or str(p).startswith(str(r) + "/") for r in roots):
        raise ValueError(
            f"文件 {p} 不在允许目录内；仅允许: {', '.join(str(r) for r in roots)}"
        )
    return p


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        ans = input(f"{prompt} [yes/no]: ").strip().lower()
    except EOFError:
        return False
    return ans in ("yes", "y")


def load_token() -> str | None:
    """从 Luban 的 machine.json 读 token（只读，不修改）。

    Luban 4.x 的实际路径是 `state.server.token`；
    早期版本/变体可能在 `server.token`，两处都找。
    """
    p = Path.home() / "Library/Application Support/snapmaker-luban/machine.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    for path in (("state", "server", "token"), ("server", "token")):
        node = data
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, str) and node:
            return node
    return None


def load_config(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path).expanduser()
    if not p.exists():
        raise SystemExit(f"配置文件不存在: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- 事件监控 ----------

class Monitor:
    def __init__(self, printer: Printer, interval: int = 10):
        self.p = printer
        self.interval = interval
        self.last: dict | None = None
        self.was_printing = False
        self.last_progress = 0.0

    def check(self) -> list[str]:
        try:
            s = self.p.status()
        except Exception as e:
            return [f"error|读取状态失败: {e}"]

        events: list[str] = []
        busy = is_busy(s)
        progress = s.get("progress") or 0.0
        fname = s.get("fileName") or "未知文件"

        if self.was_printing and not busy and progress >= 0.99:
            events.append(f"success|打印完成: {fname}（用时 {fmt_time(s.get('elapsedTime'))}）")
        if not self.was_printing and busy:
            events.append(f"info|打印开始: {fname}")

        if s.get("isFilamentOut"):
            events.append("warning|检测到缺料!")
        if busy and (s.get("nozzleTargetTemperature") or 0) > 0 and s.get("nozzleTemperature", 0) < 30:
            events.append("warning|喷嘴温度偏低")

        if self.last:
            if s.get("status") == "PAUSED" and self.last.get("status") == "RUNNING":
                events.append("warning|打印已暂停")
            if s.get("status") == "RUNNING" and self.last.get("status") == "PAUSED":
                events.append("info|打印已继续")

        if busy:
            for m in (0.25, 0.5, 0.75):
                if self.last_progress < m <= progress:
                    events.append(
                        f"info|进度 {int(m * 100)}%（预计剩余 {fmt_time(s.get('remainingTime'))}）")

        self.last, self.was_printing, self.last_progress = s, busy, progress
        return events


# ---------- CLI ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="Snapmaker 2.0 SSTP 控制 CLI")
    ap.add_argument("--host", help="打印机 IP")
    ap.add_argument("--token", help="访问 token（缺省读 Luban machine.json）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--config", help="JSON 配置 {host, token, port}")
    ap.add_argument("--yes", action="store_true", help="跳过确认（谨慎使用）")
    ap.add_argument("--allow-dir", action="append", default=[],
                    help="额外允许上传的目录（可重复）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="打印当前状态")
    sub.add_parser("status-json", help="输出状态 JSON")
    sub.add_parser("modules", help="打印模块列表与信息")
    sub.add_parser("discover", help="UDP 广播发现打印机")
    sub.add_parser("enclosure", help="查询封闭舱状态")
    sub.add_parser("camera", help="抓取摄像头图像并保存到文件")

    g = sub.add_parser("gcode", help="下发 G-code")
    g.add_argument("--code", required=True)

    sp = sub.add_parser("speed", help="设置打印速度")
    sp.add_argument("--value", type=int, required=True)

    nz = sub.add_parser("nozzle", help="设置喷嘴目标温度")
    nz.add_argument("--value", type=int, required=True)

    bd = sub.add_parser("bed", help="设置热床目标温度")
    bd.add_argument("--value", type=int, required=True)

    zo = sub.add_parser("z-offset", help="设置 Z offset")
    zo.add_argument("--value", type=float, required=True)

    lp = sub.add_parser("laser", help="设置激光功率")
    lp.add_argument("--value", type=int, required=True)

    fl = sub.add_parser("filament", help="装料/卸料")
    fl.add_argument("action", choices=["load", "unload"])

    ex = sub.add_parser("extruder", help="切换挤出机")
    ex.add_argument("index", type=int)

    pf = sub.add_parser("print-file", help="导出打印机上的最后一个文件")
    pf.add_argument("--out", help="保存路径（缺省打印到 stdout）")

    sn = sub.add_parser("send", help="上传 G-code（只准备，不启动）")
    sn.add_argument("file")
    sn.add_argument("--type", default="3DP", choices=["3DP", "Laser", "CNC"])
    sn.add_argument("--start", action="store_true", help="上传后立即启动打印")

    w = sub.add_parser("watch", help="轮询打印进度")
    w.add_argument("--interval", type=int, default=5)
    w.add_argument("--events", action="store_true", help="只输出事件（不刷新界面）")

    for name in ("start", "pause", "resume", "stop"):
        sub.add_parser(name, help=f"{name} 打印任务")

    args = ap.parse_args()

    cfg = load_config(args.config)
    host = args.host or cfg.get("host") or cfg.get("ip")
    token = args.token or cfg.get("token") or load_token()
    port = args.port or cfg.get("port", DEFAULT_PORT)

    if args.cmd == "discover":
        found = discover()
        if not found:
            print("未发现打印机。跨网段请用 --target <ip> 单播探测。", file=sys.stderr)
            return 1
        for p in found:
            print(f"{p.get('ip')}  {p.get('name', '')}  {p.get('model', '')}  {p.get('status', '')}")
        return 0

    if not token:
        print("缺少 token：用 --token/--config 指定，或先让 Luban 连接过该打印机",
              file=sys.stderr)
        return 2
    if not host:
        print("缺少 --host", file=sys.stderr)
        return 2

    pr = Printer(host, token, port)

    try:
        if args.cmd == "status":
            print(fmt_status(pr.status()))
        elif args.cmd == "status-json":
            print(json.dumps(pr.status(), ensure_ascii=False, indent=2))
        elif args.cmd == "modules":
            print(json.dumps(pr.modules(), ensure_ascii=False, indent=2))
            print(json.dumps(pr.module_info(), ensure_ascii=False, indent=2))
        elif args.cmd == "enclosure":
            print(pr.enclosure_status())
        elif args.cmd == "camera":
            data = pr.camera_image()
            out = Path(f"snapmaker_cam_{int(time.time())}.jpg")
            out.write_bytes(data)
            print(f"已保存 {out}（{len(data)} 字节）")
        elif args.cmd == "print-file":
            data = pr.print_file()
            if args.out:
                Path(args.out).write_bytes(data)
                print(f"已保存 {args.out}（{len(data)} 字节）")
            else:
                sys.stdout.buffer.write(data)
        elif args.cmd == "gcode":
            if not confirm(f"确认下发 G-code {args.code!r}?", args.yes):
                print("已取消")
                return 1
            code, reason, body = pr.execute_code(args.code)
            print(f"HTTP {code} {reason}")
            print(body.decode("latin1", errors="replace")[:2000])
        elif args.cmd == "speed":
            print(pr.set_work_speed(args.value))
        elif args.cmd == "nozzle":
            if args.value > 0 and not confirm(
                    f"确认设置喷嘴目标温度 {args.value}°C?", args.yes):
                print("已取消")
                return 1
            print(pr.set_nozzle_temp(args.value))
        elif args.cmd == "bed":
            if args.value > 0 and not confirm(
                    f"确认设置热床目标温度 {args.value}°C?", args.yes):
                print("已取消")
                return 1
            print(pr.set_bed_temp(args.value))
        elif args.cmd == "z-offset":
            print(pr.set_z_offset(args.value))
        elif args.cmd == "laser":
            print(pr.set_laser_power(args.value))
        elif args.cmd == "filament":
            fn = pr.filament_load if args.action == "load" else pr.filament_unload
            print(fn())
        elif args.cmd == "extruder":
            print(pr.switch_extruder(args.index))
        elif args.cmd == "send":
            p = check_file_allowed(args.file, args.allow_dir)
            st = pr.status()
            if is_busy(st):
                print("打印机正在打印，先 stop 再发文件", file=sys.stderr)
                return 1
            print("上传:", pr.prepare_print(str(p), args.type))
            if args.start:
                if not confirm("立即启动打印?", args.yes):
                    print("已取消启动（文件已上传，可稍后 start）")
                    return 0
                print("启动:", pr.start_print())
        elif args.cmd == "start":
            if not confirm("确认启动打印?", args.yes):
                print("已取消")
                return 1
            print(pr.start_print())
        elif args.cmd == "pause":
            print(pr.pause_print())
        elif args.cmd == "resume":
            print(pr.resume_print())
        elif args.cmd == "stop":
            if not confirm("确认停止打印?", args.yes):
                print("已取消")
                return 1
            print(pr.stop_print())
        elif args.cmd == "watch":
            if args.events:
                mon = Monitor(pr, args.interval)
                print(f"监控 {host}（每 {args.interval}s），Ctrl-C 退出")
                while True:
                    for ev in mon.check():
                        print(f"[{time.strftime('%H:%M:%S')}] {ev}")
                    time.sleep(args.interval)
            else:
                while True:
                    s = pr.status()
                    print("\033[2J\033[H", end="")
                    print(fmt_status(s))
                    if s.get("fileName"):
                        print(f"剩余时间 : {fmt_time(s.get('remainingTime'))}")
                    print("更新时间:", time.strftime("%H:%M:%S"))
                    time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止")
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
