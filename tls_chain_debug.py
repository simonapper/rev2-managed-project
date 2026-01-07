# tls_chain_debug.py
import ssl
import socket

host, port = "smtp.gmail.com", 587

# Diagnostic: do NOT verify so we can see what cert were being given
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

with socket.create_connection((host, port), timeout=10) as s:
    s.recv(4096)
    s.sendall(b"EHLO localhost\r\n")
    s.recv(4096)
    s.sendall(b"STARTTLS\r\n")
    s.recv(4096)

    with ctx.wrap_socket(s, server_hostname=host) as tls:
        der = tls.getpeercert(binary_form=True)
        pem = ssl.DER_cert_to_PEM_cert(der)
        print(pem)
