"""Minimal localtunnel client — pure Python, no dependencies beyond stdlib + requests."""
import json
import socket
import ssl
import threading
import time
import requests

LOCALTUNNEL_HOST = "localtunnel.me"
LOCAL_PORT = 8000
MAX_CONNECTIONS = 10


def get_tunnel():
    """Request a new tunnel from localtunnel.me."""
    resp = requests.get(f"https://{LOCALTUNNEL_HOST}/", params={"new": ""}, verify=False, timeout=15)
    data = resp.json()
    return data  # {"id": "...", "port": ..., "max_conn_count": ..., "url": "https://xxxx.loca.lt"}


def proxy_connection(remote_host, remote_port, local_port):
    """Connect to the remote tunnel server and proxy to localhost."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    while True:
        try:
            raw = socket.create_connection((remote_host, remote_port), timeout=30)
            remote = ctx.wrap_socket(raw, server_hostname=remote_host)

            local = socket.create_connection(("127.0.0.1", local_port), timeout=5)

            def forward(src, dst, name):
                try:
                    while True:
                        data = src.recv(8192)
                        if not data:
                            break
                        dst.sendall(data)
                except Exception:
                    pass
                finally:
                    try: src.close()
                    except: pass
                    try: dst.close()
                    except: pass

            t1 = threading.Thread(target=forward, args=(remote, local, "remote->local"), daemon=True)
            t2 = threading.Thread(target=forward, args=(local, remote, "local->remote"), daemon=True)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        except Exception as e:
            time.sleep(1)


def main():
    print("Requesting tunnel from localtunnel.me...", flush=True)
    info = get_tunnel()
    url = info["url"]
    port = info["port"]
    max_conn = info.get("max_conn_count", MAX_CONNECTIONS)

    print(f"Tunnel URL: {url}", flush=True)
    print(f"Remote port: {port}, Max connections: {max_conn}", flush=True)
    print(f"Forwarding to localhost:{LOCAL_PORT}", flush=True)

    # Open pool of connections to tunnel server
    threads = []
    for i in range(min(max_conn, MAX_CONNECTIONS)):
        t = threading.Thread(
            target=proxy_connection,
            args=(LOCALTUNNEL_HOST, port, LOCAL_PORT),
            daemon=True
        )
        t.start()
        threads.append(t)

    print(f"\n>>> Submit this URL: {url}/solve <<<\n", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down tunnel.")


if __name__ == "__main__":
    main()
