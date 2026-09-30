/**
 * Frida JS script — перехватывает сетевые запросы Chromium на Windows
 * Хуки: WinHTTP (winhttp.dll) + Winsock (ws2_32.dll)
 */

"use strict";

// ─── WinHTTP: WinHttpOpenRequest ───────────────────────────────────
try {
    var pWinHttpOpenRequest = Module.findExportByName("winhttp.dll", "WinHttpOpenRequest");
    if (pWinHttpOpenRequest) {
        Interceptor.attach(pWinHttpOpenRequest, {
            onEnter: function (args) {
                // args[1] = verb (LPCWSTR), args[2] = objectName (LPCWSTR)
                var verb = args[1].readUtf16String();
                var path = args[2].readUtf16String();
                send({
                    type: "winhttp_request",
                    verb: verb,
                    path: path,
                    ts: Date.now()
                });
            }
        });
        send({ type: "info", msg: "Hooked WinHttpOpenRequest" });
    }
} catch (e) {
    send({ type: "error", msg: "WinHttpOpenRequest hook failed: " + e });
}

// ─── WinHTTP: WinHttpSendRequest ───────────────────────────────────
try {
    var pWinHttpSendRequest = Module.findExportByName("winhttp.dll", "WinHttpSendRequest");
    if (pWinHttpSendRequest) {
        Interceptor.attach(pWinHttpSendRequest, {
            onEnter: function (args) {
                // args[1] = headers (LPCWSTR), args[3] = optional data ptr, args[4] = optional data len
                var headers = "";
                try { headers = args[1].readUtf16String(); } catch (_) {}
                var optLen = args[4].toInt32();
                var body = "";
                if (optLen > 0 && optLen < 8192) {
                    try { body = args[3].readUtf8String(optLen); } catch (_) {}
                }
                send({
                    type: "winhttp_send",
                    headers: headers,
                    bodyLen: optLen,
                    bodyPreview: body.substring(0, 2048),
                    ts: Date.now()
                });
            }
        });
        send({ type: "info", msg: "Hooked WinHttpSendRequest" });
    }
} catch (e) {
    send({ type: "error", msg: "WinHttpSendRequest hook failed: " + e });
}

// ─── WinHTTP: WinHttpReceiveResponse ──────────────────────────────
try {
    var pWinHttpReceiveResponse = Module.findExportByName("winhttp.dll", "WinHttpReceiveResponse");
    if (pWinHttpReceiveResponse) {
        Interceptor.attach(pWinHttpReceiveResponse, {
            onEnter: function (args) {
                this.hRequest = args[0];
            },
            onLeave: function (retval) {
                send({
                    type: "winhttp_response",
                    hRequest: this.hRequest.toString(),
                    success: retval.toInt32() !== 0,
                    ts: Date.now()
                });
            }
        });
        send({ type: "info", msg: "Hooked WinHttpReceiveResponse" });
    }
} catch (e) {
    send({ type: "error", msg: "WinHttpReceiveResponse hook failed: " + e });
}

// ─── Winsock: connect — ловим все TCP-соединения ──────────────────
try {
    var pConnect = Module.findExportByName("ws2_32.dll", "connect");
    if (pConnect) {
        Interceptor.attach(pConnect, {
            onEnter: function (args) {
                var sockAddr = args[1];
                var family = sockAddr.readU16();
                if (family === 2) { // AF_INET
                    var port = (sockAddr.add(2).readU8() << 8) | sockAddr.add(3).readU8();
                    var ip = sockAddr.add(4).readU8() + "." +
                             sockAddr.add(5).readU8() + "." +
                             sockAddr.add(6).readU8() + "." +
                             sockAddr.add(7).readU8();
                    send({
                        type: "tcp_connect",
                        ip: ip,
                        port: port,
                        ts: Date.now()
                    });
                }
            }
        });
        send({ type: "info", msg: "Hooked ws2_32!connect" });
    }
} catch (e) {
    send({ type: "error", msg: "connect hook failed: " + e });
}

// ─── Winsock: send — содержимое исходящих пакетов ─────────────────
try {
    var pSend = Module.findExportByName("ws2_32.dll", "send");
    if (pSend) {
        Interceptor.attach(pSend, {
            onEnter: function (args) {
                var len = args[2].toInt32();
                var preview = "";
                if (len > 0 && len < 65536) {
                    try { preview = args[1].readUtf8String(Math.min(len, 2048)); } catch (_) {}
                }
                // отправляем только если похоже на HTTP
                if (preview && (preview.startsWith("GET ") || preview.startsWith("POST ") ||
                    preview.startsWith("PUT ") || preview.startsWith("DELETE ") ||
                    preview.startsWith("PATCH ") || preview.startsWith("HEAD ") ||
                    preview.startsWith("OPTIONS "))) {
                    send({
                        type: "raw_send",
                        len: len,
                        preview: preview.substring(0, 2048),
                        ts: Date.now()
                    });
                }
            }
        });
        send({ type: "info", msg: "Hooked ws2_32!send" });
    }
} catch (e) {
    send({ type: "error", msg: "send hook failed: " + e });
}

// ─── Winsock: recv — содержимое входящих пакетов ──────────────────
try {
    var pRecv = Module.findExportByName("ws2_32.dll", "recv");
    if (pRecv) {
        Interceptor.attach(pRecv, {
            onEnter: function (args) {
                this.buf = args[1];
                this.bufLen = args[2].toInt32();
            },
            onLeave: function (retval) {
                var received = retval.toInt32();
                if (received > 0) {
                    var preview = "";
                    try { preview = this.buf.readUtf8String(Math.min(received, 512)); } catch (_) {}
                    // только HTTP-ответы
                    if (preview && preview.startsWith("HTTP/")) {
                        send({
                            type: "raw_recv",
                            len: received,
                            preview: preview.substring(0, 1024),
                            ts: Date.now()
                        });
                    }
                }
            }
        });
        send({ type: "info", msg: "Hooked ws2_32!recv" });
    }
} catch (e) {
    send({ type: "error", msg: "recv hook failed: " + e });
}

send({ type: "info", msg: "All hooks installed. Waiting for traffic..." });
