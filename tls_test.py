# tls_test.py

import ssl
import socket

host, port = "smtp.gmail.com", 587
ctx = ssl.create_default_context()

with socket.create_connection((host, port), timeout=10) as s:
    banner = s.recv(4096)
    print("BANNER:", banner)

    s.sendall(b"EHLO localhost\r\n")
    print(s.recv(4096))

    s.sendall(b"STARTTLS\r\n")
    print(s.recv(4096))

    with ctx.wrap_socket(s, server_hostname=host) as tls:
        cert = tls.getpeercert()
        print("Subject:", cert.get("subject"))
        print("Issuer:", cert.get("issuer"))
