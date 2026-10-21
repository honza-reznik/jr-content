# <ID> — <název bloku>

| | |
|---|---|
| **Fáze** | F? |
| **Stav** | ☐ plán / ▶ rozpracováno / 🔍 test-review / ✅ hotovo / ⏸ odloženo / ✖ zrušeno |
| **Kodér** | A (Claude Opus 5.5 high) / B (Codex gpt-6-astra high) |
| **Reviewer** | ten druhý |
| **Vizuální test** | ano/ne — T1 (Terra high) / T2 (Sonnet high) |
| **Větev / worktree** | `blok/<ID>` · `/home/honza/rezie-wt/<ID>` |
| **Závisí na** | |

## 1. Zadání (píše PM, kodér nemění)

**Cíl (jedna věta):**

**Kontext:** co už existuje, na co navázat (odkazy na soubory, ADR, moduly v `docs/aplikace/`).

**Akceptační kritéria** (každé ověřitelné testem nebo krokem vizuálního testu):

- [ ] AK1:
- [ ] AK2:

**Mimo rozsah:** co tento blok výslovně nedělá.

**Dotčené soubory / rozhraní:**

**Jak ověřit:** příkazy testů, scénář vizuálního testu (`docs/testy/<ID>.md`).

## 2. Průběh (píše kodér průběžně; každý zápis s časem a agentem)

- AAAA-MM-DD HH:MM [B] začátek, plán postupu: …
- …
- *Přerušeno:* kde jsem skončil, co přesně udělat dál, co je rozbité. (při limitu / konci)

## 3. Testy a review

**Automatické testy:** příkaz, výsledek (počet prošlých/neprošlých), výpis chyb.

**Code review** (reviewer): nálezy, závažnost, co bylo opraveno.

**Vizuální test:** odkaz na protokol, výsledek.

## 4. Výsledek (píše PM po mergi — toto je vývojová dokumentace kroku)

**Co vzniklo:** stručně, s odkazy na soubory a commity.

**Odchylky od zadání a proč:**

**Známá omezení / dluh:**

**Aktualizovaná dokumentace aplikace:** které soubory v `docs/aplikace/`.

**Merge:** commit v `main`, datum.
