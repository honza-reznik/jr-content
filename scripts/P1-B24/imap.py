"""Read only the explicitly authorized P1-B24 test message; do not change flags."""
import email
import imaplib
import json
import os
import re
from email.header import decode_header, make_header
mailbox = imaplib.IMAP4_SSL(os.environ['IMAP_HOST'], int(os.environ['IMAP_PORT']), timeout=20)
mailbox.login(os.environ['IMAP_USER'], os.environ['IMAP_PASS'])
found = []
try:
    status, folders = mailbox.list()
    assert status == 'OK'
    for raw in folders:
        folder = raw.rsplit(b' ', 1)[-1].strip(b'"')
        if mailbox.select(b'"' + folder + b'"', readonly=True)[0] != 'OK':
            continue
        status, result = mailbox.search(None, 'HEADER', 'Subject', '"[JRC TEST] P1-B24"')
        if status != 'OK':
            continue
        for seq in result[0].split()[-3:]:
            status, parts = mailbox.fetch(seq, '(BODY.PEEK[])')
            if status != 'OK':
                continue
            message = email.message_from_bytes(next(p[1] for p in parts if isinstance(p, tuple)))
            subject = str(make_header(decode_header(message.get('Subject', ''))))
            if '[JRC TEST] P1-B24' not in subject or 'jrctest24@' not in message.get('To',''):
                continue
            bodies = [p.get_payload(decode=True).decode(p.get_content_charset() or 'utf-8')
                      for p in message.walk() if p.get_content_type() in ('text/plain','text/html')]
            assert 'brightbean' not in (subject + ''.join(bodies)).lower()
            links = re.findall(r'http://127\.0\.0\.1:13663[^\s<>"\']+', ''.join(bodies))
            assert links and all(u.startswith('http://127.0.0.1:13663/jrc/') for u in links)
            found.append({'subject':subject, 'from':message['From'], 'to':message['To'], 'links_valid':True, 'brightbean':False})
finally:
    mailbox.logout()
assert found, 'P1-B24 mail not found'
print(json.dumps(found))
