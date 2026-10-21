# AGENTS.md — pravidla pro všechny agenty v projektu Režie

Platí pro Claude (PM, kodér, tester) i Codex (kodér, tester). Přečti celé, než začneš.

## Než začneš cokoliv dělat

1. Přečti `docs/STAV.md` (štafeta) — kde jsme, co je aktivní, kdo na čem dělá.
2. Přečti soubor svého bloku `docs/bloky/<ID>.md` — zadání a celý průběh (možná navazuješ
   na někoho, kdo skončil na limitu).
3. Podle potřeby: `docs/PLAN.md` (směr, principy), relevantní ADR v `docs/rozhodnuti/`,
   dokumentace modulů v `docs/aplikace/`.

## Při práci

- **Dělej jen svůj blok a jen to, co je v zadání.** Nic „navíc“, žádné refaktory mimo rozsah.
  Když narazíš na něco mimo rozsah, zapiš to do průběhu bloku, neopravuj to.
- Pracuj ve **větvi a worktree bloku** (`blok/<ID>`, `/home/honza/rezie-wt/<ID>`), nikdy přímo v `main`.
- **Zapisuj průběh průběžně** do sekce 2 souboru bloku (čas, agent, co je hotové) a **commituj**
  po každém smysluplném kroku. Commit zpráva začíná `<ID>: `.
- **Před koncem nebo při limitu** zapiš `*Přerušeno:* …` — kde jsi, co přesně dál, co je rozbité.
  Kdokoliv jiný musí umět pokračovat jen podle souborů.
- Testy: před předáním spusť automatické testy (od F2 celou sadu) a výsledek zapiš do sekce 3.
- Překážka, která nejde vyřešit v rámci bloku: zapiš ji do sekce 2 souboru bloku jako
  `PŘEKÁŽKA: …` a skonči s popisem. PM ji převede do `docs/prekazky.md`.
- Soubory `docs/STAV.md`, `docs/denik.md`, `docs/prekazky.md`, `docs/pro-honzu.md` a `docs/PLAN.md`
  upravuje jen PM v hlavním checkoutu `/home/honza/rezie` (větev `main`). Kodéři a testeři píšou do
  souboru svého bloku nebo testu.

## Zakázáno

- Tajné údaje (hesla, tokeny, klíče) v gitu. Patří do `.env` (v `.gitignore`).
- Cokoliv, co stojí peníze; placené API klíče; nákup kreditů.
- Cokoliv, co odchází ven ze stroje (push, nahrávání, registrace služeb), kromě schválené zálohy repa,
  kterou dělá jen PM.
- Změny systému mimo projekt (cron, systemd, Tailscale, firewall, systémové balíčky, Docker kontejnery
  jiných projektů na Jarvisovi). Ostatní kontejnery na stroji (kosync, ntfy, jrklon-*, jrreg-*, agent-*)
  nezastavuj ani neměň.
- Napojení na skutečné účty na sociálních sítích. Jen vymyšlená testovací data.
- Merge do `main` — dělá jen PM.
- Stahování velkých dat z internetu (datový limit 60 GB/měsíc). Testovací videa vyráběj lokálně
  FFmpegem.

## Jazyk a styl

- Dokumentace, zápisy a zprávy česky. Kód, identifikátory a commit zprávy anglicky nebo česky
  podle konvence v ADR-004 (do té doby anglicky).
- Zápisy pro Honzu bez žargonu; odborný termín vysvětli jednou větou.
- Commit zpráva: první řádek `<ID>: co se změnilo`, v těle řádek `Agent: <role> (<model>)`,
  např. `Agent: B (Codex gpt-6-astra high)`. Commity od Claude navíc končí
  `Co-Authored-By: Claude <model> <noreply@anthropic.com>`.
