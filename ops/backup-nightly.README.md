# Noční offsite záloha

Podklady se neinstalují automaticky. Po nastavení veřejného klíče age a vzdálené adresy v `.env`
může Honza s PM ručně nainstalovat `ops/systemd/jrc-backup.service` a
`ops/systemd/jrc-backup.timer` jako uživatelské jednotky. Časovač spouští zálohu denně ve 3:30.
`ops/backup-nightly.sh` zajišťuje zámek, denní log, nenulový návrat při chybě a pořadí
záloha → ověření → šifrování → git commit/push.

Push nastane pouze při neprázdném `JRC_BACKUP_GIT_REMOTE`. Pobočka je `zalohy`; rotace odstraňuje
staré šifrované soubory jen z její pracovní kopie, takže git historie zůstává dostupná.
