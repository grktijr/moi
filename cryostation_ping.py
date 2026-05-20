"""
Minimal Gen 1/2 Montana Cryostation connectivity test.

Protocol: ASCII over TCP. Each message is length-prefixed:
    <2-digit zero-padded length><command-or-payload>

Examples on the wire:
    "03GPT"        -> GET Platform Temperature
    "06STSP010"    -> SET Temperature Setpoint to 10 K  (payload "SP010")
    "07OK4.235"    -> typical response: status + value

We just send a few harmless GET queries. Nothing is changed on the cryostat.
"""

import socket
import sys

HOST = "192.168.0.2"     # control laptop on the private cable link
PORT = 7773              # default Cryostation TCP port
TIMEOUT = 3.0            # seconds


def send(sock: socket.socket, cmd: str) -> str:
    """Send one ASCII command, return the ASCII response (length prefix stripped)."""
    msg = f"{len(cmd):02d}{cmd}".encode("ascii")
    sock.sendall(msg)

    # Response is also length-prefixed: read 2 bytes, then that many bytes.
    header = b""
    while len(header) < 2:
        chunk = sock.recv(2 - len(header))
        if not chunk:
            raise ConnectionError("connection closed while reading length header")
        header += chunk
    n = int(header.decode("ascii"))

    body = b""
    while len(body) < n:
        chunk = sock.recv(n - len(body))
        if not chunk:
            raise ConnectionError("connection closed mid-message")
        body += chunk
    return body.decode("ascii")


def main() -> int:
    queries = [
        ("GPT", "platform temperature (K)"),
        ("GST", "sample temperature (K)"),
        ("GSS", "sample stability (K)"),
        ("GCP", "chamber pressure (mTorr)"),
        ("GCS", "compressor state"),
    ]

    try:
        with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as s:
            s.settimeout(TIMEOUT)
            print(f"connected to {HOST}:{PORT}\n")
            for cmd, label in queries:
                try:
                    reply = send(s, cmd)
                    print(f"  {cmd:6s} ({label:30s}) -> {reply!r}")
                except Exception as e:
                    print(f"  {cmd:6s} ({label:30s}) -> ERROR: {e}")
    except OSError as e:
        print(f"could not connect to {HOST}:{PORT} -- {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
