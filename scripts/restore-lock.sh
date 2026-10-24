#!/bin/sh
# F2-B09a: zámek obnovy hlavního projektu. Obnova (scripts/backup/backup.sh restore-run) drží po celou dobu výhradní
# zámek `flock` na souboru .jrc-restore.lock v kořeni pracovní kopie. Tento skript volá `make up`, `reset` a `down`
# a odmítne je, dokud zámek drží někdo jiný. Vlastní kroky obnovy zámek zdědí (deskriptor 9 míří na týž soubor) a projdou.
# Pro ověřovací projekty (jrc-<název>) nedělá nic. JRC_TEST_MAIN_PROJECT / JRC_TEST_RESTORE_LOCK jsou jen pro testy
# pojistek a nikdy nezeslabí ochranu projektu jrc.
set -eu
project=${JRC_PROJECT:-jrc}
test_main=${JRC_TEST_MAIN_PROJECT:-}
if [ "$project" != jrc ] && { [ -z "$test_main" ] || [ "$project" != "$test_main" ]; }; then exit 0; fi
lock=.jrc-restore.lock
if [ "$project" != jrc ] && [ -n "${JRC_TEST_RESTORE_LOCK:-}" ]; then lock=$JRC_TEST_RESTORE_LOCK; fi
[ -e "$lock" ] || exit 0
if [ -e /proc/self/fd/9 ] && [ "$(stat -L -c %d:%i /proc/self/fd/9 2>/dev/null)" = "$(stat -c %d:%i "$lock")" ]; then exit 0; fi
if ! { exec 8< "$lock"; } 2>/dev/null || ! flock -n 8; then
    printf 'CHYBA: Probíhá obnova projektu %s (zámek %s). Počkej na její konec; make up/reset/down teď nespouštím.\n' "$project" "$lock" >&2
    exit 1
fi
