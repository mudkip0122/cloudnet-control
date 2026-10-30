"""Client-side TLS and token settings shared by deployed Agents."""
import ipaddress
from pathlib import Path
import socket
import ssl


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def client_settings(args):
    token = Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file else None
    if args.token_file and not token:
        raise ValueError("Token file is empty")
    if bool(args.ca_file) != bool(token):
        raise ValueError("Use --ca-file and --token-file together")
    if not loopback(args.host) and not args.ca_file:
        raise ValueError("Remote connections require --ca-file and --token-file")
    context = ssl.create_default_context(cafile=args.ca_file) if args.ca_file else None
    if context is not None:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context, token


def connect(host, port, context=None):
    conn = socket.create_connection((host, port), timeout=5)
    if context is None:
        return conn
    try:
        return context.wrap_socket(conn, server_hostname=host)
    except BaseException:
        conn.close()
        raise
