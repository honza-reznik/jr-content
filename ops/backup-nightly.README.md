# Noční offsite záloha

Podklady se neinstalují automaticky. Po nastavení veřejného klíče age a vzdálené adresy v `.env`
může Honza s PM ručně nainstalovat `ops/systemd/jrc-backup.service` a
`ops/systemd/jrc-backup.timer` jako uživatelské jednotky. Časovač spouští zálohu denně ve 3:30.
`ops/backup-nightly.sh` zajišťuje zámek a denní log. Nejprve hostitel připraví místní klon
včetně existující vzdálené větve `zalohy`, potom `make backup-offsite` ověří místní zálohu,
zašifruje databázové dumpy a vytvoří commit. Nakonec hostitel provede push přímo na adresu
`JRC_BACKUP_GIT_REMOTE`. Kontejner nemá síťové připojení a push ani SSH v něm neběží.

Log obsahuje pouze předem dané stavové zprávy a návratové kódy. Výpisy zálohování, gitu ani
vzdálené adresy do něj nevstupují.

Push nastane pouze při neprázdném `JRC_BACKUP_GIT_REMOTE`. Pobočka je `zalohy`; rotace odstraňuje
staré šifrované soubory jen z její pracovní kopie, takže git historie zůstává dostupná.
