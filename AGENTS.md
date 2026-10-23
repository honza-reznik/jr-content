# AGENTS.md: pravidla pro všechny agenty v projektu JR Content

Platí pro Claude (PM, kodér, tester) i pro Codex (kodér, tester). Než začneš, přečti je celá.

**Cesty:**

- **Kód:** `/home/honza/jr-content`, worktree bloků `/home/honza/jr-content-wt/<ID>`.
- **Veškerá dokumentace** (plán, štafeta, bloky, testy, dokumentace aplikace):
  `/home/honza/jarvis/knowledge/projects/jr-content/`, dál jen `KB/`.

## Než začneš cokoliv dělat

1. Přečti `KB/vyvoj/stav.md` (štafeta): kde jsme, co je aktivní, kdo na čem dělá.
2. Přečti soubor svého bloku `KB/vyvoj/bloky/<ID>.md`, tedy zadání a celý průběh. Možná navazuješ
   na někoho, kdo skončil na limitu.
3. Podle potřeby: `KB/vyvoj/plan.md` (směr, principy), ADR v `KB/vyvoj/rozhodnuti/`, dokumentace
   modulů v `KB/aplikace/`.

## Při práci

- **Dělej jen svůj blok a jen to, co je v zadání.** Nic „navíc“, žádné refaktory mimo rozsah.
  Na co narazíš mimo rozsah, zapiš do průběhu bloku. Neopravuj to.
- Kód piš ve **větvi a worktree bloku** (`blok/<ID>`, `/home/honza/jr-content-wt/<ID>`), nikdy přímo
  v `main`.
- **Průběh zapisuj průběžně** do sekce 2 souboru bloku v `KB/` (čas, agent, co je hotové). Kód
  **commituj** do větve bloku po každém smysluplném kroku.
- **Před koncem nebo při limitu** zapiš `*Přerušeno:* …`: kde jsi, co přesně dál a co je rozbité.
  Kdokoliv jiný musí umět pokračovat jen podle souborů.
- Před předáním spusť automatické testy (od F2 celou sadu) a výsledek zapiš do sekce 3 bloku.
- Překážku, kterou nejde vyřešit v rámci bloku, zapiš do sekce 2 jako `PŘEKÁŽKA: …` a skonči
  s popisem. PM ji převede do `KB/vyvoj/prekazky.md`.
- V `KB/` smíš upravovat **jen soubor svého bloku nebo testu**. Ostatní soubory (`stav.md`,
  `denik.md`, `prekazky.md`, `pro-honzu.md`, `plan.md`, `aplikace/`) upravuje jen PM. V repozitáři
  `jarvis` **nic necommituj**, to dělá PM.

## Hranice (rozhodnutí Honzy)

- **Sestavení, instalace balíčků, běh aplikace, testy i prohlížeč pro vizuální testy spouštěj jen
  uvnitř vývojového prostředí v Dockeru** (popis v `KB/aplikace/provoz/vyvojove-prostredi.md`, příkazy
  `make …`). Na systém Jarvise nic neinstaluj.
- Aplikace je v MVP1 jen lokální. Porty navazuj jen na `127.0.0.1` a do Tailscale nesahej.
- Nedestruktivní kroky dělej sám.
- **Uvnitř Dockeru projektu JR Content** smí agent i mazat a resetovat data, pokud to blok potřebuje,
  ale **jen svého ověřovacího projektu** `JRC_PROJECT=jrc-<blok>`. Data hlavního projektu `jrc` nikdy
  (žádné `down -v`, `make reset`, `docker volume rm` nad `jrc-*-data`). Přímé `docker compose` bez
  `JRC_PROJECT` Compose odmítne (F0-B09).
- **Mimo Docker nic, co by ovlivnilo systém**, bez povolení Honzy. To zahrnuje cron a systemd,
  Tailscale, firewall, systémové balíčky a soubory mimo `KÓD/`, worktree a soubor svého bloku.
- Kontejnery jiných projektů (kosync, ntfy, jrklon-*, jrreg-*, agent-*) nezastavuj ani neměň.

## Zakázáno

- Tajné údaje (hesla, tokeny, klíče) v gitu i v KB. Patří do `.env` (je v `.gitignore`).
- Cokoliv, co stojí peníze. Placené API klíče, nákup kreditů, ruční obnova nebo navýšení limitů.
- Cokoliv, co odchází ven ze stroje: push, nahrávání, registrace služeb.
- Napojení na skutečné účty na sociálních sítích. Jen vymyšlená testovací data.
- Merge do `main`. Ten dělá jen PM.
- Stahování velkých dat z internetu (datový limit 60 GB měsíčně). Testovací videa vyráběj lokálně
  FFmpegem.

## Jazyk a styl

- Dokumentace, zápisy a zprávy piš česky. Identifikátory v kódu anglicky (do rozhodnutí v ADR-004).
- Zápisy pro Honzu bez žargonu. Odborný termín vysvětli jednou větou.
- Commit zpráva: první řádek `<ID>: co se změnilo`, v těle řádek `Agent: <role> (<model>)`, např.
  `Agent: B (Codex gpt-6-astra high)`. Commity od Claude navíc končí řádkem
  `Co-Authored-By: Claude <model> <noreply@anthropic.com>`.
