"""Validate B exam contracts, including b6f76c0 foreground events."""
from datetime import datetime
import math
import re


def text(value, limit=128):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return value


def number(value, minimum=0, maximum=1.7976931348623157e308):
    return (type(value) in (int, float) and minimum <= value <= maximum
            and (not isinstance(value, float) or math.isfinite(value)))


def exam_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value) is not None


def executable(value):
    return (text(value) and value == value.strip()
            and not any(c in value for c in '/\\:*?"<>|')
            and not any(ord(c) < 32 for c in value))


def identity(value):
    return (isinstance(value, dict) and type(value.get("pid")) is int and value["pid"] > 0
            and number(value.get("create_time"), minimum=0) and value["create_time"] > 0)


def command_error(request):
    action = request.get("action")
    if action == "STOP_PROCESSES":
        targets = request.get("targets")
        if (not isinstance(targets, list) or not 1 <= len(targets) <= 100
                or not all(identity(row) for row in targets)
                or len({row["pid"] for row in targets}) != len(targets)):
            return "INVALID_TARGETS"
    elif action == "STOP_PROCESS":
        if not identity(request):
            return "INVALID_IDENTITY"
    elif action == "SET_EXAM_MODE":
        if type(request.get("enabled")) is not bool or not exam_id(request.get("exam_id")):
            return "INVALID_EXAM_SETTINGS"
        if request["enabled"]:
            names = request.get("forbidden_processes")
            interval = request.get("poll_interval", 1)
            if ("forbidden_processes" in request and (
                    not isinstance(names, list) or not 1 <= len(names) <= 100
                    or not all(executable(name) for name in names))
                    or not number(interval, .5, 60)):
                return "INVALID_EXAM_SETTINGS"
    return None


def rtt_fields(message):
    keys = ("rtt_ms", "rtt_status", "rtt_timestamp")
    if not any(key in message for key in keys):
        return {}
    if not all(key in message for key in keys):
        raise ValueError("RTT fields must be supplied together")
    status, value, timestamp = (message["rtt_status"], message["rtt_ms"], message["rtt_timestamp"])
    if status not in ("ok", "pending", "timeout", "stale"):
        raise ValueError("Invalid RTT status")
    if (status == "ok" and (not number(value) or timestamp is None)
            or status != "ok" and value is not None):
        raise ValueError("RTT value does not match status")
    if timestamp is not None:
        stamp(timestamp)
    return {key: message[key] for key in keys}


def exam_event(message, pc_id):
    if (message.get("pc_id") != pc_id or type(message.get("version")) is not int
            or message["version"] != 1 or not text(message.get("event_id"))
            or not exam_id(message.get("exam_id"))):
        raise ValueError("Invalid exam event identity")
    stamp(message.get("timestamp"))
    kind = message.get("kind")
    fields = ("type", "version", "event_id", "pc_id", "exam_id", "kind", "timestamp")
    clean = {key: message[key] for key in fields}
    if kind == "FORBIDDEN_PROCESS_DETECTED":
        process = message.get("process")
        if not identity(process) or not executable(process.get("name")):
            raise ValueError("Invalid detected process")
        clean["process"] = {key: process[key] for key in ("pid", "name", "create_time")}
    elif kind == "MONITOR_STATUS":
        if (message.get("status") not in ("ok", "partial", "unavailable")
                or type(message.get("unreadable_count")) is not int or message["unreadable_count"] < 0
                or not number(message.get("poll_interval"), .5, 60)):
            raise ValueError("Invalid monitor status")
        clean.update({key: message[key] for key in ("status", "unreadable_count", "poll_interval")})
    elif kind == "EXAM_WINDOW_CHANGED":
        transition = message.get("transition")
        if transition not in ("left", "returned"):
            raise ValueError("Invalid foreground transition")
        for key in ("previous_process", "process"):
            process = message.get(key)
            if not identity(process) or not executable(process.get("name")):
                raise ValueError("Invalid foreground process identity")
            clean[key] = {field: process[field] for field in ("pid", "name", "create_time")}
        was_chrome = clean["previous_process"]["name"].casefold() == "chrome.exe"
        is_chrome = clean["process"]["name"].casefold() == "chrome.exe"
        if (transition == "left" and (not was_chrome or is_chrome)
                or transition == "returned" and (was_chrome or not is_chrome)):
            raise ValueError("Foreground transition does not match process names")
        clean["transition"] = transition
    elif kind == "FOREGROUND_MONITOR_STATUS":
        if message.get("status") not in ("ok", "unavailable"):
            raise ValueError("Invalid foreground monitor status")
        clean["status"] = message["status"]
    else:
        raise ValueError("Unsupported exam event kind")
    return clean


def command_result(request, message):
    if type(message.get("ok")) is not bool or not text(message.get("code")):
        raise ValueError("Invalid command result")
    if not message["ok"]:
        return
    action = request["action"]
    if action in ("SET_EXAM_MODE", "GET_EXAM_STATUS"):
        expected = ("EXAM_STATUS" if action == "GET_EXAM_STATUS" else
                    "EXAM_MODE_ENABLED" if request["enabled"] else "EXAM_MODE_DISABLED")
        mode = message.get("exam_mode")
        if message["code"] != expected or not isinstance(mode, dict) or type(mode.get("enabled")) is not bool:
            raise ValueError("Invalid exam mode result")
        if mode["enabled"]:
            check = dict(mode, action="SET_EXAM_MODE")
            if ("forbidden_processes" not in mode or command_error(check) or mode.get("monitor_status") not in
                    ("starting", "ok", "partial", "unavailable")):
                raise ValueError("Invalid enabled exam state")
        elif (mode.get("forbidden_processes") != [] or mode.get("poll_interval") is not None
              or mode.get("monitor_status") != "off"
              or mode.get("exam_id") is not None and not exam_id(mode["exam_id"])):
            raise ValueError("Invalid disabled exam state")
        if action == "SET_EXAM_MODE" and (mode["enabled"] != request["enabled"]
                                                         or mode.get("exam_id") != request["exam_id"]):
            raise ValueError("Exam state does not match command")
    elif action == "STOP_PROCESSES":
        results, targets = message.get("results"), request["targets"]
        if (message["code"] != "PROCESS_BATCH_RESULT" or not isinstance(results, list)
                or len(results) != len(targets) or type(message.get("all_terminated")) is not bool):
            raise ValueError("Invalid batch result")
        counts = {}
        allowed = ("TERMINATED", "TERMINATION_REQUESTED", "PROTECTED", "PID_REUSED", "NOT_FOUND",
                   "ACCESS_DENIED", "PROCESS_ERROR", "EXPIRED_DURING_BATCH", "BATCH_TIME_LIMIT")
        for target, result in zip(targets, results):
            if (not identity(result) or any(result[key] != target[key] for key in ("pid", "create_time"))
                    or type(result.get("ok")) is not bool or result.get("code") not in allowed):
                raise ValueError("Batch result identity mismatch")
            counts[result["code"]] = counts.get(result["code"], 0) + 1
        if message.get("counts") != counts or message["all_terminated"] != all(
                result["code"] == "TERMINATED" for result in results):
            raise ValueError("Batch result summary mismatch")
