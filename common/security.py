"""TLS transport and credentials for direct EC2 connections."""
import ipaddress
import json
from pathlib import Path
import socket
import ssl


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def client_arguments(parser):
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--ca-file", help="Trusted server certificate/CA PEM")
    parser.add_argument("--token-file", help="File containing this client's secret token")
    parser.add_argument("--server-name", help="Certificate identity to verify when the connection IP changes")


def client_settings(args):
    token = Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file else None
    if args.token_file and not token:
        raise ValueError("Token file is empty")
    if bool(args.ca_file) != bool(token):
        raise ValueError("Use --ca-file and --token-file together")
    if not loopback(args.host) and not args.ca_file:
        raise ValueError("Remote connections require --ca-file and --token-file")
    context = ssl.create_default_context(cafile=args.ca_file) if args.ca_file else None
    if context:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context, token


def connect(host, port, context=None, server_name=None):
    conn = socket.create_connection((host, port), timeout=5)
    if context is None:
        return conn
    try:
        return context.wrap_socket(conn, server_hostname=server_name or host)
    except BaseException:
        conn.close()
        raise


def load_auth(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Auth file must be a JSON object")
    agents = data.get("agents")
    admin = data.get("admin_token")
    if not isinstance(agents, dict) or not agents:
        raise ValueError("Auth file needs an agents object")
    tokens = [admin, *agents.values()]
    if any(not isinstance(t, str) or len(t) < 32 or not t.isascii() or t.strip() != t for t in tokens):
        raise ValueError("Each token must contain at least 32 ASCII characters without surrounding whitespace")
    if any(not isinstance(k, str) or not k.strip() or len(k) > 128 for k in agents):
        raise ValueError("Invalid agent PC ID")
    if len(set(tokens)) != len(tokens):
        raise ValueError("Use a different token for each agent and admin")
    return data
