import socket, ssl, time, sys, os
GEN = "generation-1029673721"
cred = "/root/argus-e2-ip1/credentials/" + GEN
ctx = ssl.create_default_context(cafile=cred + "/bundle.pem")
ctx.check_hostname = False
ctx.load_cert_chain(cred + "/svid.pem", cred + "/key.pem")
raw = socket.create_connection(("127.0.0.1", 1944), timeout=10)
tls = ctx.wrap_socket(raw, server_hostname="argus.local")
key = open("/root/argus-ip1-handoff/probe-round6-403-20261008/ip2-handoff/paper02-user-api-key").read().strip()
body = b"p"*8192 + b" e2diag-fact"
req_id = "e2diag-buffering-20261008-0001"
headers = ("POST /api/v1/search/find HTTP/1.1\r\nHost: argus.local\r\n"
           "X-Argus-Run-ID: paper-20261004t145835z\r\n"
           "X-Argus-Request-ID: " + req_id + "\r\n"
           "X-API-Key: " + key + "\r\n"
           "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n").encode()
t0 = time.time()
tls.sendall(headers)
print("headers_sent +%.1fs" % (time.time()-t0), flush=True)
chunks = [body[i:i+264] for i in range(0, len(body), 264)]
tls.sendall(b"%x\r\n" % len(chunks[0]) + chunks[0] + b"\r\n")
print("chunk0_sent +%.1fs" % (time.time()-t0), flush=True)
time.sleep(10)
for c in chunks[1:]:
    tls.sendall(b"%x\r\n" % len(c) + c + b"\r\n")
tls.sendall(b"0\r\n\r\n")
print("body_complete +%.1fs" % (time.time()-t0), flush=True)
data = b""
while True:
    part = tls.recv(4096)
    if not part: break
    data += part
print("response_received +%.1fs bytes=%d" % (time.time()-t0, len(data)), flush=True)
