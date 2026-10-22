"""Ověření ochrany místního FreeFrame přímo v běžícím workeru/plánovači."""
import os
import socket
from apps.api.config import settings

assert settings.mail_provider == 'smtp'
assert settings.smtp_host == '127.0.0.1' and settings.smtp_port == 9
assert settings.smtp_use_tls is False
assert os.environ['TRANSCODER_CPU_LIMIT'] == '4'
assert settings.stale_upload_timeout_hours == 24
assert settings.soft_delete_retention_days == 30
try:
    connection = socket.create_connection(('1.1.1.1', 443), timeout=2)
except OSError:
    print('OK: bez internetu, SMTP pouze nepoužitý místní port, CPU=4, úklid 24h/30d.')
else:
    connection.close()
    raise AssertionError('Vnitřní služba se připojila na internet.')
