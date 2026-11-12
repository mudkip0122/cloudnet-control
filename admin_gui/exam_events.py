"""B v1 exam events on the agreed, separate TLS administrator subscription."""
from collections import OrderedDict, deque
from copy import deepcopy
from datetime import datetime
import queue
import re
import socket
import ssl
import threading
import time

from common.protocol import recv_message, send_message
from common.security import connect

MONITOR_LABELS = {"starting": "검사 시작 중", "ok": "정상 검사", "partial": "일부 확인 불가",
                  "unavailable": "감시 불가", "off": "꺼짐"}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("이벤트 시각 오류")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("이벤트 시각에 시간대 필요")


def valid_exam_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value) is not None


def process_identity(process):
    if not isinstance(process, dict):
        raise ValueError("프로세스 식별 오류")
    pid, created, name = process.get("pid"), process.get("create_time"), process.get("name")
    if (type(pid) is not int or pid <= 0 or type(created) not in (int, float)
            or not 0 < created <= 1.7976931348623157e308
            or not isinstance(name, str) or not name.strip() or len(name) > 128
            or any(c in name for c in '/\\:*?"<>|') or any(ord(c) < 32 for c in name)):
        raise ValueError("프로세스 식별 오류")


def event_columns(event):
    kind = event['kind']
    if kind == 'EXAM_WINDOW_CHANGED':
        before, after = event['previous_process'], event['process']
        return ('Chrome 이탈' if event['transition'] == 'left' else 'Chrome 복귀',
                f"{before['name']} → {after['name']}", f"{before['pid']} → {after['pid']}")
    if kind == 'FOREGROUND_MONITOR_STATUS':
        return ('활성 창 감시 공백' if event['status'] == 'unavailable' else '활성 창 조회 복구/시작', '—', '—')
    if kind == 'FORBIDDEN_PROCESS_DETECTED':
        return ('금지 프로그램 감지', event['process']['name'], event['process']['pid'])
    return (MONITOR_LABELS[event['status']], f"조회 불가 {event['unreadable_count']}개", '—')


def exam_event(message):
    if (not isinstance(message, dict) or message.get("type") != "EXAM_EVENT"
            or type(message.get("version")) is not int or message["version"] != 1
            or not valid_exam_id(message.get("exam_id"))):
        raise ValueError("시험 이벤트 규격 오류")
    for key in ("event_id", "pc_id"):
        if not isinstance(message.get(key), str) or not message[key].strip() or len(message[key]) > 128:
            raise ValueError("시험 이벤트 식별 오류")
    timestamp(message.get("timestamp"))
    if message.get("kind") == "FORBIDDEN_PROCESS_DETECTED":
        process_identity(message.get('process'))
    elif message.get('kind') == 'EXAM_WINDOW_CHANGED':
        process_identity(message.get('process'))
        process_identity(message.get('previous_process'))
        before = message['previous_process']['name'].casefold() == 'chrome.exe'
        after = message['process']['name'].casefold() == 'chrome.exe'
        if ((message.get('transition'), before, after) not in
                (('left', True, False), ('returned', False, True))):
            raise ValueError('Chrome 전환 방향 오류')
    elif message.get('kind') == 'FOREGROUND_MONITOR_STATUS':
        if message.get('status') not in ('ok', 'unavailable'):
            raise ValueError('활성 창 감시 상태 오류')
    elif message.get("kind") == "MONITOR_STATUS":
        interval, count = message.get("poll_interval"), message.get("unreadable_count")
        if (message.get("status") not in ("ok", "partial", "unavailable")
                or type(interval) not in (int, float) or not .5 <= interval <= 60
                or type(count) is not int or count < 0):
            raise ValueError("감시 상태 오류")
    else:
        raise ValueError("지원하지 않는 시험 이벤트")
    return deepcopy(message)


def exam_mode(value):
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        return None
    exam_id, status = value.get("exam_id"), value.get("monitor_status")
    if value["enabled"]:
        names, interval = value.get("forbidden_processes"), value.get("poll_interval")
        if (not valid_exam_id(exam_id) or status not in ("starting", "ok", "partial", "unavailable")
                or not isinstance(names, list) or not 1 <= len(names) <= 100
                or not all(isinstance(name, str) and 0 < len(name) <= 128 for name in names)
                or type(interval) not in (int, float) or not .5 <= interval <= 60):
            return None
    elif (status != "off" or exam_id is not None and not valid_exam_id(exam_id)
          or value.get("forbidden_processes") != [] or value.get("poll_interval") is not None):
        return None
    return deepcopy(value)


class ExamStore:
    """Session-local, bounded history; current mode comes from server snapshots."""
    def __init__(self, limit=500, dedup_limit=4096):
        self.history = deque(maxlen=limit)
        self.seen = OrderedDict()
        self.dedup_limit = dedup_limit
        self.devices = {}
        self.monitor = {}
        self.revision = 0
        self.latest_warning = None
        self.latest_foreground = None
        self.foreground = {}
        self.foreground_stream_connected = False

    def snapshot(self, devices):
        previous = self.devices
        self.devices = {d["pc_id"]: deepcopy(d) for d in devices if isinstance(d, dict) and isinstance(d.get("pc_id"), str)}
        for pc_id in tuple(self.foreground):
            device, old = self.devices.get(pc_id, {}), previous.get(pc_id, {})
            mode, old_mode = exam_mode(device.get('exam_mode')), exam_mode(old.get('exam_mode'))
            if (not device.get('online') or not mode or not mode['enabled'] or not old_mode
                    or device.get('agent_session_id') != old.get('agent_session_id')
                    or any(mode.get(k) != old_mode.get(k) for k in ('enabled', 'exam_id', 'forbidden_processes', 'poll_interval'))
                    or mode.get('monitor_status') == 'starting' and
                    device.get('exam_state_received_at') != old.get('exam_state_received_at')):
                self.foreground.pop(pc_id, None)
        for pc_id in tuple(self.monitor):
            device = self.devices.get(pc_id, {})
            mode = exam_mode(device.get("exam_mode"))
            event = self.monitor[pc_id]
            if (not device.get("online") or not mode or not mode["enabled"]
                    or mode["exam_id"] != event["exam_id"]):
                self.monitor.pop(pc_id, None)

    def status_update(self, device):
        if isinstance(device, dict) and isinstance(device.get("pc_id"), str):
            self.snapshot([*filter(lambda d: d['pc_id'] != device['pc_id'], self.devices.values()), device])

    def invalidate(self):
        self.monitor.clear()
        self.foreground.clear()
        for device in self.devices.values():
            device['exam_mode'] = None

    def accept(self, message, apply_current=True):
        event = exam_event(message)
        key = event["event_id"]
        if key in self.seen:
            return False
        self.seen[key] = None
        if len(self.seen) > self.dedup_limit:
            self.seen.popitem(last=False)
        self.history.append(event)
        self.revision += 1
        device = self.devices.get(event['pc_id'], {})
        mode = exam_mode(device.get('exam_mode'))
        active = apply_current and device.get('online') and mode and mode['enabled'] and mode['exam_id'] == event['exam_id']
        if event['kind'] in ('EXAM_WINDOW_CHANGED', 'FOREGROUND_MONITOR_STATUS'):
            if event['kind'] == 'EXAM_WINDOW_CHANGED':
                self.latest_foreground = event
            if active and self.foreground_stream_connected:
                self.foreground[event['pc_id']] = event
        elif event["kind"] == "FORBIDDEN_PROCESS_DETECTED":
            self.latest_warning = event
        else:
            if active:
                self.monitor[event['pc_id']] = event
        return True

    def foreground_state(self, pc_id, connected):
        device = self.devices.get(pc_id, {})
        mode = exam_mode(device.get('exam_mode'))
        if not connected or not device.get('online'):
            return '연결 끊김 · 감시 공백'
        if not mode:
            return '시험 상태 미확인 · 활성 창 감시 공백'
        if not mode['enabled']:
            return '시험 모드 꺼짐 · 활성 창 감시 중지'
        if not self.foreground_stream_connected:
            return '이벤트 연결 미확인 · 활성 창 감시 공백'
        event = self.foreground.get(pc_id)
        if not event:
            return '활성 창 감시 미확인 · 지원/초기 상태 대기'
        if event.get('status') == 'unavailable':
            return '감시 공백 · 활성 창 확인 불가'
        return '활성 창 조회 가능 (최근 수신 상태)'

    def state(self, pc_id, connected):
        device = self.devices.get(pc_id, {})
        mode = exam_mode(device.get('exam_mode'))
        if not connected or not device.get('online'):
            return "연결 끊김 · 상태 미확인"
        if mode is None:
            return "시험 상태 미확인 · 서버 지원/상태 조회 필요"
        # DEVICE_LIST/STATUS_UPDATE are authoritative, including another admin's changes.
        return f"{mode.get('exam_id') or '시험 없음'} · {MONITOR_LABELS[mode['monitor_status']]}"


class ExamEventClient:
    """No command jobs here. Polling keeps this independent admin session alive.

    Events have a bounded FIFO separate from latest-only dashboard metrics. Queue
    overflow is reported as a gap and forces reconnect, never silent replacement.
    """
    def __init__(self, host, port, context, token, server_name=None, capacity=2048):
        self.host, self.port, self.context, self.token = host, port, context, token
        self.server_name = server_name
        self.events = queue.Queue(maxsize=capacity)
        self.connection = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.sock = None
        self.generation = 0
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def publish_connection(self, connected, label):
        try:
            self.connection.get_nowait()
        except queue.Empty:
            pass
        self.connection.put_nowait((self.generation, connected, label))

    def run(self):
        while not self.stop.is_set():
            self.generation += 1
            try:
                if self.context is None or not self.token:
                    raise ValueError("TLS 관리자 인증 필요")
                with connect(self.host, self.port, self.context, self.server_name) as conn:
                    self.sock = conn
                    if self.stop.is_set():
                        return
                    send_message(conn, dict(type='REGISTER', role='admin', token=self.token,
                                            subscribe_exam=True, subscribe_status=True))
                    response = recv_message(conn)
                    if response.get('type') != 'REGISTER_RESULT' or response.get('ok') is not True:
                        self.publish_connection(False, '시험 이벤트 인증/구독 거절 · 서버 지원 확인')
                        return
                    self.publish_connection(True, '관리자 연결됨 · 시험 이벤트 수신 대기 (서버 중계 지원 필요, 감시 정상 보증 아님)')
                    while not self.stop.is_set():
                        send_message(conn, {'type': 'LIST_DEVICES'})
                        deadline = time.monotonic() + 5
                        while not self.stop.is_set():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError()
                            conn.settimeout(remaining)
                            message = recv_message(conn)
                            kind = message.get('type')
                            if kind == 'EXAM_EVENT':
                                message = exam_event(message)
                            elif kind == 'DEVICE_LIST':
                                if not isinstance(message.get('devices'), list):
                                    raise ValueError('시험 상태 목록 오류')
                            elif kind == 'STATUS_UPDATE':
                                if not isinstance(message.get('device'), dict):
                                    raise ValueError('시험 상태 변경 오류')
                            else:
                                raise ValueError('구독 응답 오류')
                            self.events.put_nowait((self.generation, message))
                            if kind == 'DEVICE_LIST':
                                break
                        self.stop.wait(1)
            except ssl.SSLCertVerificationError:
                self.publish_connection(False, '시험 이벤트 인증서 오류 · 재실행 필요')
                return
            except queue.Full:
                self.publish_connection(False, '이벤트 수신 큐 초과 · 이력 공백 가능 · 재연결 중')
            except (OSError, EOFError, ValueError, UnicodeError):
                if not self.stop.is_set():
                    self.publish_connection(False, '시험 이벤트 연결 끊김/응답 오류 · 이력 공백 가능 · 재연결 중')
            finally:
                self.sock = None
            self.stop.wait(3)

    def close(self):
        self.stop.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
