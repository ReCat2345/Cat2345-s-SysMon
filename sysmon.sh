#!/usr/bin/env bash
# SysMon 后台运行脚本
#
# 用法:
#   ./sysmon.sh start     启动（默认）
#   ./sysmon.sh stop      停止
#   ./sysmon.sh restart   重启
#   ./sysmon.sh status    查看状态
#   ./sysmon.sh logs      跟踪日志（Ctrl+C 只退出查看）
#
# 可通过环境变量覆盖:
#   SYSMON_HOST=127.0.0.1
#   SYSMON_PORT=8088
#   SYSMON_DATA_DIR=/var/lib/sysmon
#   SYSMON_PYTHON=/path/to/python
#
# 原理: nohup + setsid 让进程脱离当前 SSH 会话，PID 写入 .sysmon.pid，
# 日志写入 logs/sysmon.log，因此断开 SSH 不会停止服务。

set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

APP="$APP_DIR/app.py"
PID_FILE="$APP_DIR/.sysmon.pid"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/sysmon.log"
HOST="${SYSMON_HOST:-127.0.0.1}"
PORT="${SYSMON_PORT:-8088}"
DATA_DIR="${SYSMON_DATA_DIR:-}"

info() { printf '%s\n' "$*"; }
error() { printf '错误: %s\n' "$*" >&2; }

find_python() {
  if [ -n "${SYSMON_PYTHON:-}" ]; then
    printf '%s\n' "$SYSMON_PYTHON"
    return 0
  fi
  local candidate
  for candidate in "$APP_DIR/.venv/bin/python" "$APP_DIR/venv/bin/python"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  return 1
}

read_pid() {
  if [ -f "$PID_FILE" ]; then
    tr -d '[:space:]' < "$PID_FILE" 2>/dev/null || true
  fi
}

is_running() {
  local pid="${1:-}"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

cleanup_stale_pid() {
  local pid
  pid="$(read_pid)"
  if [ -n "$pid" ] && ! is_running "$pid"; then
    rm -f "$PID_FILE"
  fi
}

start() {
  cleanup_stale_pid
  local pid
  pid="$(read_pid)"
  if is_running "$pid"; then
    info "SysMon 已在运行（PID $pid），无需重复启动。"
    return 0
  fi

  if [ ! -f "$APP" ]; then
    error "找不到 $APP"
    return 1
  fi

  local python
  if ! python="$(find_python)"; then
    error "找不到 Python，请先创建并激活 venv，或设置 SYSMON_PYTHON。"
    return 1
  fi
  if ! "$python" -c 'import psutil' >/dev/null 2>&1; then
    error "当前 Python 缺少 psutil：$python"
    info "请执行: $python -m pip install -r $APP_DIR/requirements.txt"
    return 1
  fi

  mkdir -p "$LOG_DIR"
  local args=("$APP" --host "$HOST" --port "$PORT")
  if [ -n "$DATA_DIR" ]; then
    args+=(--data-dir "$DATA_DIR")
  fi
  printf '\n===== %s start (host=%s port=%s) =====\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$HOST" "$PORT" >> "$LOG_FILE"

  if command -v setsid >/dev/null 2>&1; then
    # 由内层 sh 写入真实 PID：即使 setsid 自己 fork，PID 也不会错。
    nohup setsid /bin/sh -c 'echo $$ > "$1"; shift; exec "$@"' \
      sh "$PID_FILE" "$python" "${args[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
  else
    nohup "$python" "${args[@]}" >> "$LOG_FILE" 2>&1 < /dev/null &
    echo "$!" > "$PID_FILE"
  fi

  sleep 1
  pid="$(read_pid)"
  if is_running "$pid"; then
    local shown_host="$HOST"
    [ "$HOST" = "0.0.0.0" ] && shown_host="127.0.0.1"
    info "SysMon 已启动（PID $pid）"
    info "地址: http://$shown_host:$PORT/"
    info "日志: $LOG_FILE"
    return 0
  fi

  rm -f "$PID_FILE"
  error "启动失败，最近日志："
  tail -n 30 "$LOG_FILE" >&2 2>/dev/null || true
  return 1
}

stop() {
  local pid
  pid="$(read_pid)"
  if ! is_running "$pid"; then
    rm -f "$PID_FILE"
    info "SysMon 未运行。"
    return 0
  fi

  info "正在停止 SysMon（PID $pid）..."
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for ((i = 0; i < 20; i++)); do
    if ! is_running "$pid"; then
      break
    fi
    sleep 0.5
  done
  if is_running "$pid"; then
    error "进程未在 10 秒内退出，强制结束。"
    kill -KILL "$pid" 2>/dev/null || true
    sleep 0.5
  fi
  rm -f "$PID_FILE"
  info "SysMon 已停止。"
}

status() {
  cleanup_stale_pid
  local pid
  pid="$(read_pid)"
  if ! is_running "$pid"; then
    info "SysMon 未运行。"
    return 0
  fi
  info "SysMon 正在运行（PID $pid）"
  ps -p "$pid" -o pid=,etime=,args= 2>/dev/null || true
  local shown_host="$HOST"
  [ "$HOST" = "0.0.0.0" ] && shown_host="127.0.0.1"
  info "地址: http://$shown_host:$PORT/"
  info "日志: $LOG_FILE"
}

logs() {
  if [ ! -f "$LOG_FILE" ]; then
    info "日志文件还不存在：$LOG_FILE"
    return 0
  fi
  tail -n 100 -f "$LOG_FILE"
}

usage() {
  cat <<'EOF'
用法: ./sysmon.sh [start|stop|restart|status|logs]

  start     后台启动（默认）
  stop      停止
  restart   重启
  status    查看运行状态
  logs      跟踪日志（Ctrl+C 只退出查看，不影响服务）

环境变量:
  SYSMON_HOST       监听地址，默认 127.0.0.1
  SYSMON_PORT       监听端口，默认 8088
  SYSMON_DATA_DIR   自定义数据目录
  SYSMON_PYTHON     指定 Python，默认使用 .venv/bin/python 或 python3

示例:
  SYSMON_HOST=0.0.0.0 SYSMON_PORT=8088 ./sysmon.sh start
EOF
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop && start ;;
  status) status ;;
  logs) logs ;;
  help | -h | --help) usage ;;
  *)
    usage
    exit 2
    ;;
esac
