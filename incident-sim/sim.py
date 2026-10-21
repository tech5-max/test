#!/usr/bin/env python3
"""Incident simulator: a fake API that misbehaves on purpose, for verifying the
load-test tools. Standard library only.

SCENARIO (env), applied once the incoming rate goes above FAIL_ABOVE_RPS:
  healthy    always fast 200s
  slow       latency grows with load (queueing), never errors
  timeout    stops answering (requests hang until the client times out)
  5xx        answers 500
  refused    stops listening and drops open connections (connection refused)
  reset      resets every connection without answering
  stray5xx   healthy, except exactly STRAY_COUNT 500s in total
  ratelimit  429 with RateLimit-* headers on most requests (rate limiter on)
"""
import asyncio
import collections
import json
import os
import socket
import struct
import time

SCENARIO = os.environ.get("SCENARIO", "healthy")
FAIL_ABOVE = float(os.environ.get("FAIL_ABOVE_RPS", "100"))
BASE_MS = float(os.environ.get("BASE_LATENCY_MS", "20"))
STRAY_COUNT = int(os.environ.get("STRAY_COUNT", "2"))
BAN_AFTER = int(os.environ.get("BAN_AFTER_REQUESTS", "1500"))
BAN_SECONDS = float(os.environ.get("BAN_SECONDS", "8"))
PORT = int(os.environ.get("PORT", "8080"))

arrivals = collections.deque()
state = {"tripped": False, "stray_left": STRAY_COUNT, "server": None, "conns": set(), "requests": 0}


def current_rps():
    now = time.monotonic()
    arrivals.append(now)
    while arrivals and arrivals[0] < now - 1:
        arrivals.popleft()
    return len(arrivals)


def response(status, body, extra=""):
    reason = {200: "OK", 429: "Too Many Requests", 500: "Internal Server Error"}.get(status, "OK")
    data = json.dumps(body).encode()
    head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: keep-alive\r\n{extra}\r\n")
    return head.encode() + data


def hard_reset(writer):
    sock = writer.get_extra_info("socket")
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # close → RST
    except OSError:
        pass
    writer.transport.abort()


async def _ban_cycle():
    # Close the listener (connections are refused), then reopen after the ban.
    srv = state["server"]
    if srv:
        srv.close()
    await asyncio.sleep(BAN_SECONDS)
    state["tripped"] = False
    newsrv = await asyncio.start_server(handle, "0.0.0.0", PORT, backlog=4096, reuse_address=True)
    state["server"] = newsrv
    print("sim: ban lifted; accepting connections again", flush=True)


async def handle(reader, writer):
    state["conns"].add(writer)
    try:
        while True:
            # Read one HTTP request (headers only; GET has no body).
            head = await reader.readuntil(b"\r\n\r\n")
            if not head:
                break
            state["requests"] += 1
            rps = current_rps()
            over = rps > FAIL_ABOVE
            if over and not state["tripped"] and SCENARIO in ("timeout", "5xx", "refused", "reset"):
                state["tripped"] = True
                print(f"sim: {SCENARIO} triggered at ~{rps} req/s", flush=True)
                if SCENARIO == "refused" and state["server"]:
                    state["server"].close()  # stop accepting new connections
                    for w in list(state["conns"]):
                        w.close()
                    return
            # fail2ban-style ban: after BAN_AFTER total requests from the flood,
            # stop accepting connections for BAN_SECONDS, then reopen (unban).
            if SCENARIO == "ban" and not state["tripped"] and state["requests"] >= BAN_AFTER:
                state["tripped"] = True
                print(f"sim: ban triggered after {state['requests']} requests; dropping all connections for {BAN_SECONDS}s", flush=True)
                asyncio.get_event_loop().create_task(_ban_cycle())
                for w in list(state["conns"]):
                    try:
                        w.close()
                    except Exception:
                        pass
                return
            failing = state["tripped"] or (over and SCENARIO in ("timeout", "5xx"))

            if SCENARIO == "timeout" and failing:
                await asyncio.sleep(3600)
                return
            if SCENARIO == "reset" and failing:
                hard_reset(writer)
                return
            if SCENARIO == "refused" and failing:
                writer.close()
                return
            if SCENARIO == "5xx" and failing:
                await asyncio.sleep(BASE_MS / 1000)
                writer.write(response(500, {"success": False, "error": "simulated failure"}))
                await writer.drain()
                continue
            if SCENARIO == "stray5xx" and state["requests"] > 50 and state["stray_left"] > 0:
                state["stray_left"] -= 1
                writer.write(response(500, {"success": False, "error": "stray failure"}))
                await writer.drain()
                continue
            if SCENARIO == "ratelimit" and state["requests"] % 4 != 1:
                writer.write(response(429, {"success": False, "message": "Too many requests"},
                                      "RateLimit-Limit: 60\r\nRateLimit-Remaining: 0\r\nRateLimit-Reset: 30\r\n"))
                await writer.drain()
                continue

            delay = BASE_MS
            if SCENARIO == "slow" and over:
                delay = min(5000, BASE_MS + (rps - FAIL_ABOVE) * 40)  # queueing: slower as load grows
            await asyncio.sleep(delay / 1000)
            extra = "RateLimit-Limit: 60\r\n" if SCENARIO == "ratelimit" else ""
            writer.write(response(200, {"success": True, "rps": rps}, extra))
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
        pass
    finally:
        state["conns"].discard(writer)
        try:
            writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT, backlog=4096, reuse_address=True)
    state["server"] = server
    print(f"sim: scenario={SCENARIO} fail_above={FAIL_ABOVE} req/s on :{PORT}", flush=True)
    async with server:
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            pass
    # "refused": the listener is closed; keep the process alive so the
    # container (and its address) stays up while connections are refused.
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
