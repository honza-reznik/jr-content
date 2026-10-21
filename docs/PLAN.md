# Režie — plán vývoje a dokumentace

> Pracovní název aplikace: **Režie**. Je to vlastní nástroj agentury na tvorbu obsahu na sociální sítě:
> od nápadu přes scénář a zpětnou vazbu k videu až po schválený popisek. Název jde změnit, v kódu ani
> v cestách na něm nic nestojí.

**Vlastník projektu:** Honza · **Projektový manažer:** Claude (samostatná session) · **Založeno:** 2026-10-21
**Repozitář:** `/home/honza/rezie` na Jarvisovi · **Záloha:** viz [F0-B02](#f0--příprava-a-orchestrace)

---

## 0. Jak tento dokument funguje

Tento soubor je **plán i dokumentace zároveň**. Na začátku popisuje, co se má udělat. Jak vývoj
postupuje, mění se hotové části na popis toho, co se skutečně udělalo a proč.

**Pravidla zápisu (platí pro každého agenta):**

1. **Stav bloku** v roadmapě (sekce 7) se mění jen takto:
   `☐ plán` → `▶ rozpracováno` → `🔍 test/review` → `✅ hotovo`. Dál jsou `⏸ odloženo` a `✖ zrušeno`.
   Odložení i zrušení vždy s důvodem a odkazem na překážku nebo rozhodnutí.
2. **Každý blok má svůj soubor** `docs/bloky/<ID>.md`. Je to zadání, deník práce i výsledek. Vzniká
   z [šablony](bloky/_SABLONA.md). Až je blok hotový, jeho sekce *Výsledek* je **vývojová
   dokumentace** toho kroku.
3. **Hotový blok aktualizuje dokumentaci aplikace** v `docs/aplikace/` (architektura, moduly, provoz,
   uživatelská příručka). Tak průběžně vzniká **dokumentace aplikace**.
4. **Po dokončení fáze** projektový manažer přepíše popis fáze v sekci 6. Místo *„co se udělá“*
   tam bude *„co se udělalo“*, s odkazy na bloky a testy, a doplní *Shrnutí fáze*. Původní plán zůstává
   dohledatelný v souborech bloků a v gitu.
5. **Plán se zpřesňuje postupně.** Fáze, na které se zrovna pracuje, a ta následující musí mít bloky
   rozepsané detailně. Vzdálenější fáze stačí hrubě. Na začátku každé fáze ji projektový manažer
   rozepíše na bloky. Pokud se mění rozsah, schvaluje to Honza (sekce 2.2).
6. **Změny plánu** se zapisují do [Deníku změn plánu](#13-deník-změn-plánu) na konci tohoto souboru.

**Související soubory (všechny v `docs/`):**

| Soubor | K čemu je | Kdo zapisuje |
|---|---|---|
| [STAV.md](STAV.md) | **Štafeta.** Co se právě děje, kdo na čem pracuje, další krok, limity. Čte se jako první. | PM po každém kroku, kodér při přerušení |
| [pro-honzu.md](pro-honzu.md) | Otázky a schválení, na které se čeká na Honzu | PM; Honza odpovídá |
| [prekazky.md](prekazky.md) | Evidence překážek a jejich řešení | PM (kodéři a testeři je hlásí v souboru bloku) |
| [denik.md](denik.md) | Chronologický deník práce, jeden řádek na akci | každý agent |
| `bloky/<ID>.md` | Zadání, průběh a výsledek bloku | PM zadává, kodér píše průběh, PM uzavírá |
| `testy/<ID>.md` | Testovací scénáře a protokoly | PM zadává, tester vyplňuje |
| `rozhodnuti/ADR-xxx.md` | Architektonická a zásadní rozhodnutí | PM, schvaluje Honza (jsou-li zásadní) |
| `aplikace/` | Dokumentace aplikace (vzniká průběžně) | po každém hotovém bloku |
| `zpravy/AAAA-MM-DD.md` | Ranní zpráva pro Honzu po noční práci | PM |
| [prompt-projektovy-manazer.md](prompt-projektovy-manazer.md) | Pokyny pro Claude jako projektového manažera | Honza (+ PM návrhy změn) |

---

## 1. Cíl a rozsah

### 1.1 Vize

Jedno místo, kde agentura s klientem připraví obsah od nápadu po schválení:

- **scénář** jako text s komentáři a historií,
- **sestřih videa** s komentáři ke konkrétní vteřině a s verzemi,
- **popisek** pro každou síť,
- **schválení** klientem,
- **přístup pro AI přes API**, aby s texty mohla pracovat i AI.

Publikace na sítě přijde až po MVP přes externí službu s API (kandidát Zernio, první 2 účty zdarma).
Vlastní aplikace u Mety tak nebude potřeba.

### 1.2 MVP (cíl tohoto plánu)

**Lokální verze bez napojení na publikační platformu**, běžící na Jarvisovi a dostupná Honzovi přes
Tailscale. Klientský přístup se v MVP testuje lokálně, testovacím klientským účtem a sdíleným odkazem.

MVP je hotové, když projde **akceptační scénář „Reel pro Ellu“** (T4, sekce 8). Musí v něm fungovat:

1. Honza založí klienta *Ella* s kanály Instagram a vlastní kanál *Forendors*.
2. Založí obsah „Reel o …“ a projde stavy: nápad → scénář → natáčení → střih → kontrola → schváleno →
   připraveno k publikaci → publikováno (ručně).
3. Napíše scénář v textovém editoru (nadpisy, seznamy, checklist). Klient ho okomentuje u konkrétního
   místa v textu. Honza upraví text a historie verzí ukáže, co se změnilo. Starší verze jde obnovit.
4. Nahraje sestřih v1. Klient přidá komentáře ke konkrétním vteřinám a klik na komentář přeskočí
   přehrávač na dané místo. Honza nahraje v2, verze jdou přepínat a každá má své komentáře.
5. Honza napíše popisek pro IG. Klient ho upraví nebo okomentuje. Hlídá se limit znaků IG.
6. Klient schválí scénář, video i finální příspěvek, přes svůj účet i přes sdílený odkaz bez účtu.
   Je vidět, kdo a kdy schválil.
7. Přehled všeho obsahu klienta podle stavu a v kalendáři podle data publikace.
8. AI přes API (a MCP) přečte a zapíše scénář, popisek a komentáře. Ověřeno skutečným průchodem
   s Claude.
9. Finální video a popisek jdou stáhnout a zkopírovat pro ruční publikaci.
10. Data (databáze i videa) se zálohují a obnova ze zálohy je vyzkoušená.

### 1.3 Mimo rozsah MVP (vědomě odloženo)

- publikace na sítě (Zernio a podobné), plánovač odesílání,
- veřejný přístup pro Ellu přes internet (Tailscale Funnel) a bezpečnostní audit pro veřejný provoz,
- e-mailové notifikace a přihlašování odkazem z e-mailu (v MVP jen upozornění uvnitř aplikace),
- mobilní aplikace (MVP je responzivní web, musí se dát používat na telefonu),
- analytika, inbox, automatické DM, AI asistent uvnitř aplikace,
- návrhy úprav ve stylu Google Docs (zvážit po MVP, viz F4),
- migrace obsahu z Notionu.

---

## 2. Principy vývoje

### 2.1 Bloky jako lego

- Vyvíjí se v **co nejmenších, ale smysluplných blocích**. Jeden blok má jeden jasný výstup, který jde
  samostatně otestovat. Typicky je to práce na jedno zadání pro kodéra.
- Každý blok projde stejným cyklem:
  **zadání → implementace → testy kódu → code review druhým kodérem → (vizuální test) → merge →
  dokumentace**.
- Bloky do sebe zapadají přes **jasná rozhraní**: datový model, API, komponenty. Rozhraní se mění jen
  vědomě a zapisuje se do ADR.
- Hlavní větev `main` je **vždy funkční**. Rozpracovaná práce žije ve větvi `blok/<ID>` (git worktree).
  Do `main` se dostane až po splnění akceptačních kritérií bloku.

### 2.2 Kompas: jak se nenechat vykolejit

Směr drží tři neměnné věci: **vize (1.1), definice MVP (1.2) a principy (2.x)**. Bez Honzova souhlasu
se nemění.

Každá překážka se zapíše do [prekazky.md](prekazky.md) a projektový manažer zvolí jednu cestu:

1. **Vyřešit v rámci bloku**, pokud nemění rozsah ani rozhraní.
2. **Nový blok**: řešení je samostatná práce. Zařadí se do roadmapy a do Deníku změn plánu.
3. **Odložit po MVP**: překážka se týká něčeho, bez čeho MVP projde. Přesune se do sekce 6, Po MVP.
4. **Eskalovat Honzovi** do [pro-honzu.md](pro-honzu.md), když řešení mění MVP nebo vizi, něco stojí
   peníze, mění systém mimo projekt nebo odchází ven ze stroje. Do odpovědi se pracuje na jiném bloku,
   který na překážce nezávisí.

**Pojistka proti točení v kruhu:** blok, který dvakrát po sobě neprojde testy nebo review ze stejného
důvodu, se zastaví. PM ho rozdělí na menší bloky, nebo eskaluje. Pravidlo je výchozí, upraví se podle
zkušeností.

### 2.3 Ověřování

- Nic není hotové, dokud to není ověřené testem a zapsané. „Mělo by to fungovat“ se nepočítá.
- Kód píše jeden agent a review dělá **jiný**. Kód od Codexu kontroluje Claude a naopak.
- Vizuální test dělá třetí agent podle **přesného scénáře**, ne podle vlastního uvážení.

### 2.4 Bezpečnost a peníze

- Žádné tajné údaje (hesla, tokeny, klíče) v gitu. Patří do `.env` mimo verzování, v gitu je jen
  `.env.example`.
- Nic, co stojí peníze. Žádné nákupy, kredity ani placené API klíče. Claude i Codex běží jen
  v rámci předplatného.
- Nic ven ze stroje bez Honzova souhlasu. Výjimkou je záloha repozitáře, až ji Honza schválí (F0-B02).
- Žádné napojení na skutečné účty na sociálních sítích. Testuje se jen s vymyšlenými daty.
- Změny systému mimo projekt (cron, systemd, Tailscale, firewall, balíčky systému) jen po schválení
  Honzou.

---

## 3. Tým a role

| Role | Kdo | Model a nastavení | Co dělá |
|---|---|---|---|
| Vlastník | Honza | — | Schvaluje milníky, zásadní rozhodnutí a změny rozsahu. Odpovídá v `pro-honzu.md`. Není programátor. |
| Projektový manažer (PM) | Claude, samostatná session jen pro tento projekt | Claude Opus 5.5 | Rozepisuje bloky, píše zadání, přiděluje práci, kontroluje výsledky, merguje, vede dokumentaci a štafetu, píše ranní zprávy. Sám velké kusy kódu nepíše. |
| Kodér A | Claude | Opus 5.5, effort high (subagent) | Implementace bloků, code review práce kodéra B. |
| Kodér B | Codex | `gpt-6-astra`, reasoning high | Implementace bloků, code review práce kodéra A. |
| Vizuální tester 1 | Codex + Playwright | `gpt-5.6-terra`, reasoning high | Proklikání podle scénáře v `docs/testy/`, screenshoty, protokol. |
| Vizuální tester 2 | Claude + Playwright | Sonnet 5.5, effort high (subagent) | Totéž. Používá se střídavě s testerem 1, nebo když jeden narazí na limit. |

**Přidělování práce:** PM střídá kodéry podle dostupnosti limitů. U každého bloku zapíše do jeho
souboru, kdo blok implementuje a kdo dělá review. Kodér, který psal kód, nikdy nedělá review vlastního
kódu.

**Známé riziko limitů:** jedno kolo Astry dříve vyčerpalo celé pětihodinové okno limitu Codexu.
Proto bloky pro Astru musí být malé a PM sleduje časy obnovení limitů ve [STAV.md](STAV.md).

---

## 4. Provozní režim

### 4.1 Časové okno

- **Noc 21:00–7:00 (čas Jarvise):** vývoj běží samostatně.
- **Přes den** jen s Honzovým povolením. Honza řekne PM „můžeš pokračovat i přes den“ a PM zapíše
  povolení s datem a časem konce do `ops/povoleni-den` (formát v F0-B04). Bez platného povolení se
  přes den nespouští žádný kodér ani tester. PM ve dne jen odpovídá Honzovi a připravuje zadání.
- V 6:30 PM přestane zadávat nové bloky, dokončí rozpracované a do 7:00 zapíše
  [ranní zprávu](zpravy/) a štafetu.

### 4.2 Štafeta (navazování přes limity)

Každý agent musí kdykoliv umět skončit a kdokoliv jiný musí umět pokračovat. Proto:

1. Veškerý stav je v souborech: `STAV.md`, soubor bloku, git. Nic důležitého nesmí zůstat jen
   v kontextu konverzace.
2. Kodér zapisuje průběh do souboru bloku **průběžně**, aspoň po každém dokončeném dílčím kroku,
   a commituje do větve bloku. Commit zprávy začínají ID bloku.
3. Když agent narazí na limit nebo končí, zapíše do souboru bloku *„Přerušeno: kde jsem, co dál“*.
4. **Limit kodéra:** PM předá blok druhému kodérovi. Ten začne čtením souboru bloku a větve.
5. **Limit PM (Claude):** noční spouštěč (F0-B04/B05) zkouší PM znovu spustit, typicky každých 30 minut.
   Mezitím může Codex podle `STAV.md` jen **dokončovat aktivní blok**, který mu byl přidělen.
   Neplánuje, nemerguje, nemění plán.
6. Časy obnovení limitů zapisuje PM do `STAV.md`, aby věděl, koho kdy použít.

### 4.3 Komunikace s Honzou

- **Ranní zpráva** `docs/zpravy/AAAA-MM-DD.md`: co se v noci udělalo, co čeká na Honzu, překážky,
  plán na další noc. Krátce, česky, bez žargonu.
- **Otázky a schválení** v [pro-honzu.md](pro-honzu.md). PM čeká na odpověď a mezitím dělá jinou práci.
- Do logu aktivity Jarvise (`/home/honza/jarvis/knowledge/projects/jarvis-dev/aktivita.md`) PM zapíše
  jeden řádek za každou noc nebo session (pravidlo v `knowledge/meta/jak-logovat-aktivitu.md`).

---

## 5. Architektura (výchozí návrh, potvrdí se ve fázi 1)

### 5.1 Moduly

| Modul | Odpovědnost | Fáze |
|---|---|---|
| Účty a role | přihlášení, role admin a klient, sdílené odkazy | F2, F7 |
| Klienti a kanály | klient (workspace), jeho kanály: IG, FB, LinkedIn, TikTok, YouTube, vlastní | F2 |
| Obsah | jednotka práce (např. jeden reel), stavy a přechody, termíny | F3 |
| Komentáře | sdílené jádro komentářů pro scénář, video i popisek, veřejné a interní | F3 |
| Scénář | textový editor, komentáře ukotvené v textu, historie verzí | F4 |
| Video | nahrání, převod pro přehrávání, přehrávač, časové komentáře, verze | F5 |
| Popisek | text pro každý kanál, limity, historie | F6 |
| Schvalování | kroky schválení, kdo a kdy, klientský pohled | F7 |
| Přehled | nástěnka podle stavů, kalendář, „čeká na mě“ | F8 |
| API a MCP | rozhraní pro AI a budoucí publikaci | F9 |
| Publikace | adaptér na externí službu (Zernio a podobné) | po MVP |

### 5.2 Infrastruktura (předpoklad)

- Docker Compose na Jarvisovi: aplikace, databáze (PostgreSQL), úložiště souborů s rozhraním S3
  (Garage nebo MinIO, videa leží na disku Jarvise), worker na převod videí (FFmpeg).
- Přístup pro Honzu přes Tailscale (Serve, jen v rámci Tailnetu). Funnel pro klienty až po MVP.
- Zálohy databáze a médií. Kam přesně, rozhodne F2-B06 s Honzou. Záloha musí být mimo disk Jarvise.

### 5.3 Klíčové rozhodnutí: na čem stavět (ADR-001, fáze 1)

| Varianta | Popis | Pro | Proti |
|---|---|---|---|
| A | Fork [Brightbean Studio](https://github.com/brightbeanxyz/brightbean-studio) (AGPL) + napojený [FreeFrame](https://github.com/Techiebutler/freeframe) (MIT) na video | hotové klienty, schvalování odkazem, komentáře, verze příspěvků, API/MCP | zásahy do cizího kódu, slučování jejich nových verzí, dvě aplikace |
| B | Vlastní aplikace + FreeFrame jako služba na video přes API | čistý vlastní kód, video už hotové | dvě aplikace, závislost na mladém projektu |
| C | Vlastní aplikace, hotové knihovny (editor Tiptap, přehrávač) místo hotových aplikací | jedna aplikace, plná kontrola | nejvíc práce, hlavně video |

Kritéria rozhodnutí: kvalita a testovatelnost kódu základu, jak snadno jde rozšířit o editor scénářů,
výkon převodu videa na CPU i7-3630QM, licence, aktivita projektu, náročnost údržby. Doporučení
připraví PM, schvaluje Honza (milník M1).

---

## 6. Fáze (obecný plán)

| Fáze | Cíl | Milník (schvaluje Honza) |
|---|---|---|
| F0 | Příprava: repozitář, záloha, nástroje, noční spouštění, cvičná noc | M0: štafeta funguje |
| F1 | Průzkum základů a architektura | M1: schválené ADR-001 (na čem stavět) |
| F2 | Kostra aplikace: běží, přihlášení, klienti a kanály, zálohy | M2: kostra běží na Jarvisovi |
| F3 | Obsah, stavy a komentářové jádro | — |
| F4 | Scénáře | M3: scénář s klientem funguje |
| F5 | Video a zpětná vazba | M4: video review funguje |
| F6 | Popisek | — |
| F7 | Schvalování a klientský přístup | M5: klient projde schválení |
| F8 | Přehled a kalendář | — |
| F9 | API a MCP pro AI | — |
| F10 | Stabilizace a akceptace MVP | **M6: MVP hotové** |

**Po MVP (zatím jen hrubě, zpřesní se po M6):**

- P1: pilot s Ellou přes internet. Tailscale Funnel, bezpečnostní kontrola, e-mailové přihlášení.
- P2: publikace přes externí API (Zernio, záložně Upload-Post), plánovač odesílání.
- P3: e-mailové notifikace.
- P4: další klienti, šablony obsahu.
- P5: vylepšení pro mobil.
- P6: převod scénářů z Notionu.

*(Po dokončení fáze sem PM doplní „Shrnutí fáze“: co se skutečně udělalo, odchylky od plánu, odkazy.)*

---

## 7. Roadmapa (bloky)

Legenda velikosti: **S** = malé zadání, **M** = střední. Velké bloky (L) se nepřipouštějí, rozdělí se.
Sloupec *Kdo* = kodér / reviewer (A = Claude Opus, B = Codex Astra). Doplňuje PM při zadání.

### F0 — Příprava a orchestrace

| ID | Blok | Výstup | Závisí na | Velikost | Kdo | Stav |
|---|---|---|---|---|---|---|
| F0-B01 | Repozitář a kostra dokumentace | tento plán, šablony, AGENTS.md, prompt PM | — | S | Claude (zakládající session) | ✅ |
| F0-B02 | Záloha repozitáře | privátní vzdálený repozitář, push po každém mergi do `main` a po každé noci | F0-B01, **souhlas Honzy** | S | PM | ☐ |
| F0-B03 | Ověření nástrojů | funkční volání všech rolí: Claude subagent Opus/Sonnet high, `codex exec` Astra/Terra high, Playwright pro oba, síť a Docker v sandboxu Codexu, žádné API klíče; zápis do `docs/aplikace/provoz/nastroje.md` | F0-B01 | S | PM | ☐ |
| F0-B04 | Spouštěč štafety `ops/tick.sh` | skript: časové okno, denní povolení, zámek proti dvojímu běhu, spuštění PM, rozpoznání limitu, záložní Codex jen pro aktivní blok, log; testy skriptu | F0-B03 | M | B / A | ☐ |
| F0-B05 | Noční plánovač | systemd user timer nebo cron 21:00–6:30 po 30 min | F0-B04, **souhlas Honzy** | S | PM | ☐ |
| F0-B06 | Cvičná noc | jeden cvičný blok projde celým cyklem (zadání → kód → review → vizuální test → merge → dokumentace), včetně simulace limitu | F0-B05 | S | A + B | ☐ |

**Test F0 (T2):** protokol cvičné noci. Prošlo každé předání? Je ve STAV.md a v bloku vše potřebné
pro pokračování? Vznikla ranní zpráva? **Milník M0.**

### F1 — Průzkum základů a architektura

| ID | Blok | Výstup | Závisí na | Velikost | Kdo | Stav |
|---|---|---|---|---|---|---|
| F1-B01 | Brightbean Studio: instalace a audit | běží lokálně v Dockeru bez napojení na sítě; zpráva: struktura, technologie, testy, kvalita kódu, kde by šel přidat editor, licence, aktivita | M0 | M | A | ☐ |
| F1-B02 | FreeFrame: instalace a audit | běží lokálně; zpráva: časové komentáře, verze, odkaz pro klienta, API, úložiště; **měření převodu videa** (1 min 1080p) na Jarvisovi | M0 | M | B | ☐ |
| F1-B03 | Prototyp editoru scénáře | malý samostatný prototyp: Tiptap (nebo alternativa), komentáře ukotvené k textu, historie verzí; zpráva, co jde snadno a co ne | M0 | M | A nebo B | ☐ |
| F1-B04 | Varianty architektury, ADR-001 | srovnání A/B/C podle kritérií v 5.3 s výsledky F1-B01 až B03, doporučení | F1-B01–B03 | S | PM | ☐ |
| F1-B05 | Datový model a technologie | ADR-002 datový model a hranice modulů, ADR-003 konvence API, ADR-004 technologie a nástroje testů | F1-B04 + M1 | S | PM + A | ☐ |

**Test F1:** každý audit má reprodukovatelný postup instalace a naměřená čísla. **Milník M1:** Honza
schválí ADR-001. Po M1 PM podle zvolené varianty rozepíše fáze F2 a F3 do detailu (bloky níže jsou
výchozí návrh pro variantu B nebo C; u varianty A se část bloků zmenší nebo zmizí).

### F2 — Kostra aplikace

| ID | Blok | Výstup | Velikost | Stav |
|---|---|---|---|---|
| F2-B01 | Struktura repozitáře a Docker Compose | aplikace + DB + S3 + worker, `.env.example`, `make up` | M | ☐ |
| F2-B02 | Kvalita a testy | lint, typová kontrola, testovací framework, `make test`, kontrola před commitem | S | ☐ |
| F2-B03 | Přihlášení a role | admin a klient, odhlášení, ochrana stránek | M | ☐ |
| F2-B04 | Klienti a kanály | založení, úprava, archivace klienta; kanály IG, FB, LI, TT, YT, vlastní | M | ☐ |
| F2-B05 | Základ rozhraní | rozvržení, navigace, vzhled, funkční na telefonu | M | ☐ |
| F2-B06 | Zálohy a obnova | skript záloh DB + médií, rotace, **vyzkoušená obnova**; cíl zálohy mimo Jarvis schválí Honza | S | ☐ |
| F2-B07 | Přístup přes Tailscale Serve | aplikace dostupná Honzovi v Tailnetu (změna Tailscale = **souhlas Honzy**) | S | ☐ |

**Testy F2:** T1 u každého bloku; T2 vizuální scénář „přihlášení → založení klienta Ella s kanály“
na desktopu i telefonu (viewport 390×844); T2 obnova ze zálohy. **Milník M2.**

### F3 — Obsah, stavy a komentáře

| ID | Blok | Výstup | Velikost | Stav |
|---|---|---|---|---|
| F3-B01 | Model obsahu a stavy | obsah, výchozí stavy (sekce 1.2 bod 2), povolené přechody | M | ☐ |
| F3-B02 | Nástěnka obsahu | sloupce podle stavů, filtr podle klienta a kanálu | M | ☐ |
| F3-B03 | Detail obsahu | záložky Scénář, Video, Popisek, Schválení, Historie (zatím prázdné) | S | ☐ |
| F3-B04 | Komentářové jádro | vlákna, odpovědi, vyřešeno, veřejné a interní; jedno jádro pro všechny moduly | M | ☐ |
| F3-B05 | Historie změn | záznam kdo, co a kdy u obsahu | S | ☐ |

**Testy F3:** T2 vizuální „založení obsahu a průchod stavy“; T2 komentáře (interní komentář klient
nevidí).

### F4 — Scénáře (zpřesní se na začátku fáze)

F4-B01 editor (nadpisy, seznamy, checklist, formátování) · F4-B02 šablona scénáře (hook, hlavní
část, CTA, poznámky k natáčení) · F4-B03 komentáře ukotvené v textu · F4-B04 historie verzí,
porovnání, obnovení · F4-B05 režim čtení pro natáčení (velké písmo, bez rozhraní) ·
*(návrhy úprav ve stylu Google Docs = kandidát po MVP)*.
**Test T3 oblast Scénáře:** celý scénář s klientem (sekce 1.2 bod 3). **Milník M3.**

### F5 — Video (zpřesní se na začátku fáze)

F5-B01 nahrání velkých souborů do S3 (s pokračováním po přerušení) · F5-B02 převod pro přehrávání
a náhledy (podle ADR-001 vlastní FFmpeg nebo FreeFrame) · F5-B03 přehrávač a časové komentáře
(klik = skok na čas) · F5-B04 verze videa a komentáře ke každé verzi · F5-B05 stažení finální verze ·
*(kreslení do záběru = volitelné, rozhodne PM podle náročnosti)*.
**Test T3 oblast Video:** sekce 1.2 bod 4, včetně videa 1080p o délce 1–3 min. **Milník M4.**

### F6 — Popisek

F6-B01 popisek společný a pro každý kanál, limit znaků (IG 2 200), první komentář · F6-B02 komentáře
a historie popisku · F6-B03 náhled příspěvku (napodobenina IG).
**Test T2:** sekce 1.2 bod 5.

### F7 — Schvalování a klient

F7-B01 schvalovací kroky (scénář, video, finál), kdo a kdy · F7-B02 klientský pohled (jen jeho obsah,
„čeká na tebe“) · F7-B03 sdílený odkaz bez účtu (token, platnost, oprávnění: komentovat, upravit
popisek, schválit) · F7-B04 upozornění v aplikaci.
**Test T3 oblast Klient:** sekce 1.2 bod 6, přes účet i přes odkaz, na telefonu. **Milník M5.**

### F8 — Přehled

F8-B01 kalendář podle data publikace · F8-B02 „čeká na mě“ pro admina i klienta · F8-B03 ruční
označení „publikováno“ a export pro ruční publikaci.
**Test T2:** sekce 1.2 body 7 a 9.

### F9 — API a MCP

F9-B01 REST API pro obsah, scénář, popisek, komentáře a schválení; tokeny a oprávnění · F9-B02
MCP server nad API · F9-B03 průchod s Claude přes MCP (napíše scénář, okomentuje, upraví popisek).
**Test T3 oblast AI:** sekce 1.2 bod 8.

### F10 — Stabilizace a akceptace MVP

F10-B01 automatické end-to-end testy celého průchodu (Playwright) · F10-B02 bezpečnost (oprávnění,
sdílené odkazy, nahrávání souborů), výkon · F10-B03 zálohy: obnova nanečisto · F10-B04 dokončení
dokumentace: uživatelská příručka, provoz, architektura · F10-B05 opravy z akceptace.
**Test T4: akceptace MVP s Honzou.** **Milník M6.**

---

## 8. Testování

| Úroveň | Kdy | Co | Kdo | Kde je výsledek |
|---|---|---|---|---|
| **T1 blok** | každý blok před mergem | automatické testy kódu, lint, typová kontrola, code review druhým kodérem; u UI bloků krátký vizuální test | kodér, reviewer, tester | sekce *Testy* v souboru bloku |
| **T2 fáze** | konec fáze | integrační testy modulů fáze + vizuální scénář fáze (desktop i telefon) | PM zadá, tester provede | `docs/testy/F<n>-*.md` |
| **T3 oblast** | po dokončení oblasti (Scénáře, Video, Klient, AI) | celý průchod oblastí napříč moduly, i s chybovými stavy (velké video, přerušené nahrávání, neplatný odkaz) | 2 testeři nezávisle (Terra + Sonnet) | `docs/testy/T3-<oblast>.md` |
| **T4 MVP** | F10 | akceptační scénář „Reel pro Ellu“ (1.2) automaticky i ručně s Honzou | PM + Honza | `docs/testy/T4-mvp.md` |

**Regresní pravidlo:** od F2 se před každým mergem do `main` spouští všechny automatické testy, ne
jen testy bloku. Rozbitý `main` má přednost před vším ostatním.

**Vizuální test podle přesného zadání.** Scénář v `docs/testy/` obsahuje:

- výchozí stav a data,
- kroky,
- u každého kroku očekávaný výsledek,
- viewporty (desktop 1440×900, telefon 390×844),
- kam uložit screenshoty.

Tester výsledek nehodnotí podle sebe. U každého kroku zapíše *prošlo / neprošlo / nejasné*
a připojí screenshot. Nález se stane úkolem v bloku nebo novým blokem.

---

## 9. Překážky

Evidují se v [prekazky.md](prekazky.md): ID, datum, kdo narazil, popis, dopad na MVP, zvolená cesta
podle 2.2, stav, odkaz na řešení. Vyřešené překážky zůstávají v evidenci. Jsou součástí
dokumentace („proč je to udělané takhle“).

---

## 10. Dokumentace aplikace

Vzniká průběžně v `docs/aplikace/`:

- `README.md`: rozcestník, co aplikace umí (aktualizuje se s každou fází),
- `architektura.md`: moduly, datový model, jak spolu části mluví,
- `moduly/<modul>.md`: co modul dělá, jeho rozhraní a omezení,
- `provoz/`: instalace, spuštění, zálohy a obnova, nástroje, řešení problémů,
- `prirucka/`: uživatelská příručka pro Honzu a pro klienta (od F7).

Pravidlo: **blok není hotový, dokud neaktualizoval dokumentaci, které se týká.**

---

## 11. Rizika

| Riziko | Dopad | Opatření |
|---|---|---|
| Limity Claude a Codexu (Astra dřív vyčerpala celé okno za jedno kolo) | zpomalení | malé bloky, štafeta, střídání kodérů, časy obnovení ve STAV.md |
| Převod videa na starém CPU (i7-3630QM, 8 vláken) bude pomalý | čekání na náhled videa | měření v F1-B02, jen nutné kvality, převod na pozadí |
| Mladé open source projekty (FreeFrame v1.3) | chyby, opuštění projektu | audit v F1, varianta C jako záloha |
| AGPL u Brightbean (pokud varianta A) | povinnost zveřejnit upravený kód uživatelům | v pořádku, zohlednit v F0-B02 (repo může být veřejné) |
| Datový limit Wi-Fi 60 GB/měs. | v MVP malý (jen lokální testy) | testovací videa vyrábět lokálně (FFmpeg), ne stahovat; neomezený internet před pilotem s Ellou |
| Jediný disk Jarvise | ztráta dat | zálohy mimo stroj (F2-B06), záloha repa (F0-B02) |
| Agent „vylepšuje“ mimo zadání | rozbitý směr | kompas 2.2, review proti akceptačním kritériím bloku |

---

## 12. Milníky

| Milník | Podmínka | Schválil | Datum |
|---|---|---|---|
| M0 | cvičná noc prošla, štafeta funguje | | |
| M1 | ADR-001 schváleno | | |
| M2 | kostra běží na Jarvisovi, zálohy ověřené | | |
| M3 | scénář s klientem funguje (T3 Scénáře) | | |
| M4 | video review funguje (T3 Video) | | |
| M5 | klient projde schválení (T3 Klient) | | |
| M6 | MVP: T4 akceptace prošla | | |

---

## 13. Deník změn plánu

| Datum | Změna | Proč | Schválil |
|---|---|---|---|
| 2026-10-21 | Plán založen | zadání Honzy: vlastní nástroj místo Sequence/Planable, MVP lokálně bez publikace | Honza |
