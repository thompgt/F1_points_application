import socket
s = socket.socket()
try:
    s.settimeout(2)
    s.connect(('127.0.0.1', 8000))
    print('open')
except Exception as e:
    print('closed', e)
finally:
    s.close()
