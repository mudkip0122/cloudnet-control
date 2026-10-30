"""Register this PC with the loopback development server."""
import argparse
import socket
import threading
import time

from common.protocol import recv_message, send_message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pc-id", required=True, help="Your stable, unique PC identifier")
    parser.add_argument("--pc-name", default=socket.gethostname())
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--live", action="store_true", help="Continuously send real metrics; reconnect on network failure")
    args = parser.parse_args()
    if args.live:
        from agent.collect import samples
        while True:
            try:
                with socket.create_connection(("127.0.0.1", args.port), timeout=5) as conn:
                    register(conn, args)
                    # Read server errors/EOF while the collection loop sends.
                    failed = threading.Event()
                    def receive():
                        try:
                            conn.settimeout(None)
                            print("Server response:", recv_message(conn), flush=True)
                        except (OSError, EOFError, ValueError):
                            pass
                        finally:
                            failed.set()
                    reader = threading.Thread(target=receive, daemon=True)
                    reader.start()
                    try:
                        for message in samples(args.pc_id):
                            if failed.is_set():
                                raise ConnectionError("Server closed the session")
                            send_message(conn, message)
                    finally:
                        try:
                            conn.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        reader.join(timeout=2)
            except KeyboardInterrupt:
                return
            except (OSError, EOFError) as exc:
                print("Connection lost; retrying in 3 seconds:", exc, flush=True)
                try:
                    time.sleep(3)
                except KeyboardInterrupt:
                    return
        return
    with socket.create_connection(("127.0.0.1", args.port), timeout=5) as conn:
        register(conn, args)


def register(conn, args):
    send_message(conn, {"type": "REGISTER", "role": "agent", "pc_id": args.pc_id, "pc_name": args.pc_name})
    response = recv_message(conn)
    print(response, flush=True)
    if response.get("type") != "REGISTER_RESULT" or response.get("ok") is not True:
        raise SystemExit("Registration failed")


if __name__ == "__main__":
    main()
