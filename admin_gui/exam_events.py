"""B v1 exam events on the agreed, separate TLS administrator subscription."""
from collections import OrderedDict, deque
from copy import deepcopy
from datetime import datetime, timezone
import queue
import re
import socket
import ssl
import threading
import time
from uuid import uuid4

from common.protocol import recv_message, send_message
from common.security import connect

MONITOR_LABELS = {"starting": "검사 시작 중", "ok": "정상 검사", "partial": "일부 확인 불가",
                  "unavailable": "감시 불가", "off": "꺼짐"}
CHROME_REASONS = {
    "awaiting_extension": "확장 연결 대기", "extension_disconnected": "Chrome 확장 단절",
    "local_store_unavailable": "로컬 이벤트 저장소 확인 불가", "event_buffer_overflow": "이벤트 일부 누락",
}
CAMERA_LABELS = {"on": "켜짐", "off": "꺼짐", "permission_denied": "권한 거부", "unavailable": "사용 불가"}
CHROME_KINDS = ("EXAM_TAB_CHANGED", "EXAM_CAMERA_STATUS", "EXAM_TAB_MONITOR_STATUS")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("이벤트 시각 오류")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("이벤트 시각에 시간대 필요")
    return stamp.astimezone(timezone.utc)


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
    if kind == 'EXAM_TAB_CHANGED':
        label = {'left': '시험 탭 이탈', 'returned': '시험 탭 복귀', 'closed': '시험 탭 닫힘'}[event['transition']]
        duration = event.get('away_seconds')
        return label, f"이탈 {duration:.2f}초" if duration is not None else '—', '—'
    if kind == 'EXAM_CAMERA_STATUS':
        return '카메라 상태', CAMERA_LABELS[event['status']], '—'
    if kind == 'EXAM_TAB_MONITOR_STATUS':
        if event['status'] == 'monitoring':
            return '탭 감시 시작/복구', 'Chrome 확장 연결', '—'
        details = CHROME_REASONS[event['reason']]
        if 'dropped_count' in event:
            details += f" ({event['dropped_count']}건)"
        return '탭 감시 공백', details, '—'
    if kind == 'TIMELINE_BOUNDARY':
        labels = {'agent_lost': 'Agent 단절', 'agent_restored': 'Agent 재등록',
                  'stream_lost': '관리자 이벤트 수신 공백', 'exam_ended': '시험 관찰 종료'}
        return labels[event['reason']], '—', '—'
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
    kind = message.get('kind')
    if kind in CHROME_KINDS:
        fields = ('type', 'version', 'event_id', 'pc_id', 'exam_id', 'kind', 'timestamp')
        if kind == 'EXAM_TAB_CHANGED':
            if message.get('transition') not in ('left', 'returned', 'closed'):
                raise ValueError('시험 탭 전환 오류')
            fields += ('transition',)
        elif kind == 'EXAM_CAMERA_STATUS':
            if message.get('status') not in tuple(CAMERA_LABELS):
                raise ValueError('카메라 상태 오류')
            fields += ('status',)
        else:
            status, reason = message.get('status'), message.get('reason')
            if ((status, reason) != ('monitoring', 'extension_connected')
                    and not (status == 'unavailable' and reason in tuple(CHROME_REASONS))):
                raise ValueError('시험 탭 감시 상태 오류')
            count = message.get('dropped_count')
            if reason == 'event_buffer_overflow':
                if type(count) is not int or count <= 0:
                    raise ValueError('누락 건수 오류')
                fields += ('dropped_count',)
            elif 'dropped_count' in message:
                raise ValueError('누락 건수 오류')
            fields += ('status', 'reason')
        return {field: message[field] for field in fields}
    if kind == "FORBIDDEN_PROCESS_DETECTED":
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
        self.timeline_devices = {}
        self.interrupted_exams = {}
        self.chrome = {}
        self.chrome_devices = set()

    def snapshot(self, devices, track_connections=True):
        previous = self.devices
        self.devices = {d["pc_id"]: deepcopy(d) for d in devices if isinstance(d, dict) and isinstance(d.get("pc_id"), str)}
        if track_connections:
            self.track_timeline_devices(self.devices)
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
        self.chrome_stream_gap()
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
        device = self.devices.get(event['pc_id'], {})
        mode = exam_mode(device.get('exam_mode'))
        active = apply_current and device.get('online') and mode and mode['enabled'] and mode['exam_id'] == event['exam_id']
        if event['kind'] in CHROME_KINDS:
            if active:
                self.chrome_devices.add(event['pc_id'])
                self.apply_chrome_event(event)
        elif event['kind'] in ('EXAM_WINDOW_CHANGED', 'FOREGROUND_MONITOR_STATUS'):
            if event['kind'] == 'EXAM_WINDOW_CHANGED':
                self.latest_foreground = event
            if active and self.foreground_stream_connected:
                self.foreground[event['pc_id']] = event
        elif event["kind"] == "FORBIDDEN_PROCESS_DETECTED":
            self.latest_warning = event
        else:
            if active:
                self.monitor[event['pc_id']] = event
        self.history.append(event)
        self.revision += 1
        return True

    def timeline_boundary(self, pc_id, exam_id, reason, received=None):
        try:
            timestamp(received)
        except (ValueError, OverflowError):
            received = datetime.now(timezone.utc).isoformat()
        self.history.append(dict(kind='TIMELINE_BOUNDARY', event_id=str(uuid4()), pc_id=pc_id,
                                 exam_id=exam_id, reason=reason, timestamp=received,
                                 status='monitoring' if reason == 'agent_restored' else 'unavailable'))
        self.revision += 1
        self.chrome.pop(pc_id, None)

    def track_timeline_devices(self, devices):
        previous = self.timeline_devices
        self.timeline_devices = devices
        for pc_id in (previous.keys() | devices.keys()) & self.chrome_devices:
            old, current = previous.get(pc_id, {}), devices.get(pc_id, {})
            before, after = exam_mode(old.get('exam_mode')), exam_mode(current.get('exam_mode'))
            received = current.get('server_timestamp')
            if old.get('online') and before and before['enabled']:
                if not current.get('online') or current.get('agent_session_id') != old.get('agent_session_id'):
                    self.interrupted_exams[pc_id] = before['exam_id']
                    self.timeline_boundary(pc_id, before['exam_id'], 'agent_lost', received)
                elif (not after or not after['enabled'] or after['exam_id'] != before['exam_id']
                      or after['monitor_status'] == 'starting' and
                      current.get('exam_state_received_at') != old.get('exam_state_received_at')):
                    self.timeline_boundary(pc_id, before['exam_id'], 'exam_ended', received)
            if current.get('online') and pc_id in self.interrupted_exams:
                self.timeline_boundary(pc_id, self.interrupted_exams.pop(pc_id), 'agent_restored', received)

    def chrome_stream_gap(self):
        for pc_id, device in self.timeline_devices.items():
            mode = exam_mode(device.get('exam_mode'))
            if pc_id in self.chrome_devices and device.get('online') and mode and mode['enabled']:
                self.timeline_boundary(pc_id, mode['exam_id'], 'stream_lost')
        self.chrome.clear()
        self.timeline_devices = {}

    def apply_chrome_event(self, event):
        pc_id, kind = event['pc_id'], event['kind']
        state = self.chrome.setdefault(pc_id, {})
        observed = timestamp(event['timestamp'])
        # Delayed records remain in history. They cannot reopen an older observation.
        if state.get('observed', observed) > observed and kind != 'EXAM_TAB_MONITOR_STATUS':
            state.pop('left', None)
            return
        state['observed'] = max(observed, state.get('observed', observed))
        if kind == 'EXAM_TAB_MONITOR_STATUS':
            if event['status'] == 'unavailable':
                state.update(monitoring=False, camera=None, left=None)
                # Overflow is reported after draining the batch and carries no gap start time.
                for row in reversed(self.history):
                    if row['pc_id'] != pc_id or row['exam_id'] != event['exam_id']:
                        continue
                    if row['kind'] == 'TIMELINE_BOUNDARY':
                        break
                    duration = row.get('away_seconds')
                    if duration is not None and (event['reason'] == 'event_buffer_overflow'
                                                  or 0 <= (timestamp(row['timestamp']) - observed).total_seconds()
                                                  <= duration):
                        row.pop('away_seconds', None)
            else:
                state.update(monitoring=True, camera=None, left=None)
        elif kind == 'EXAM_CAMERA_STATUS':
            state['camera'] = event['status']
        elif event['transition'] == 'left':
            if state.get('monitoring'):
                state['left'] = observed
        else:
            left = state.pop('left', None)
            if event['transition'] == 'returned' and left is not None:
                event['away_seconds'] = (observed - left).total_seconds()
            elif event['transition'] == 'closed':
                state['camera'] = None

    def camera_state(self, pc_id):
        device = self.devices.get(pc_id, {})
        mode = exam_mode(device.get('exam_mode'))
        if not device.get('online') or not mode or not mode['enabled']:
            return '관찰 중지' if mode and not mode['enabled'] else '미확인'
        return CAMERA_LABELS.get(self.chrome.get(pc_id, {}).get('camera'), '미확인')

    def remove_history(self, event_ids):
        """Remove local display records, retaining dedup and current monitoring state."""
        ids = set(event_ids)
        remaining = [event for event in self.history if event['event_id'] not in ids]
        removed = len(self.history) - len(remaining)
        if not removed:
            return 0
        self.history = deque(remaining, maxlen=self.history.maxlen)
        self.latest_warning = next((event for event in reversed(self.history)
            if event['kind'] == 'FORBIDDEN_PROCESS_DETECTED'), None)
        self.latest_foreground = next((event for event in reversed(self.history)
            if event['kind'] == 'EXAM_WINDOW_CHANGED'), None)
        self.revision += 1
        return removed

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
