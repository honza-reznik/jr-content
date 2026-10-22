# Noční plánovač (systemd user timer)

Uživatelský timer (běží pod uživatelem `honza`, ne systémově), schváleno P-002.

Instalace / aktualizace:

    cp ops/systemd/jrc-tick.service ops/systemd/jrc-tick.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now jrc-tick.timer

Kontrola: `systemctl --user list-timers jrc-tick.timer`, log `ops/logs/tick-AAAA-MM-DD.log`.
Vypnutí: `systemctl --user disable --now jrc-tick.timer`.
