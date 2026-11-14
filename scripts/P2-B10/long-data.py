"""Dlouhá vymyšlená metadata pro kontrolu zalamování; jen vlastní ověřovací projekt."""
import os

from apps.accounts.models import User
from apps.jrc_video.models import VideoVersion

assert os.environ.get('JRC_PROJECT') == 'jrc-p2b10'
User.objects.filter(email='owner-p2b10@example.invalid').update(name='Vymyšlený autor – ' + 'DlouhéJméno' * 15)
for version in VideoVersion.objects.all():
    version.original_filename = f'Žlutý klip v{version.number} – ' + 'DlouhýNázev' * 15 + '.MOV'
    version.save(update_fields=['original_filename'])
print('OK: dlouhé názvy souborů a autora pro vlastní ověřovací data')
