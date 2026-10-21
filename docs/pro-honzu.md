# Pro Honzu — otázky a schválení

> PM sem píše, na co čeká. Honza odpovídá přímo do tohoto souboru (sekce *Odpověď*) nebo v chatu
> s PM; PM odpověď zapíše sem. Vyřízené body se nepřesouvají ani nemažou, jen se označí ✅.
> Piš srozumitelně, bez žargonu; u každé volby uveď doporučení.

## P-001 Záloha repozitáře (F0-B02) — ☐ čeká

**Otázka:** Kam zálohovat repozitář projektu (plán, dokumentace, kód)? Je to akce, která posílá data
ven ze stroje, proto potřebuje souhlas.

**Doporučení:** privátní repozitář na GitHubu pod účtem `honza-reznik` (stejně jako repozitář
`jarvis`), push po každém mergi do `main` a na konci každé noci. Na Jarvisovi chybí nástroj `gh`;
repozitář založí Honza ručně na github.com (2 minuty, PM navede), push pak jde přes existující SSH klíč.

**Odpověď:**

## P-002 Noční spouštění (F0-B05) — ☐ čeká

**Otázka:** Smí PM nastavit na Jarvisovi plánovač (systemd user timer), který mezi 21:00 a 6:30
každých 30 minut spustí `ops/tick.sh`? Je to změna systémové konfigurace. Jde kdykoliv vypnout
jedním příkazem.

**Doporučení:** ano, až po otestování skriptu v F0-B04.

**Odpověď:**

## P-003 Přístup přes Tailscale Serve (F2-B07) — ☐ čeká (stačí do fáze F2)

**Otázka:** Smí PM přidat do Tailscale Serve na Jarvisovi novou cestu pro aplikaci (jen v Tailnetu,
ne veřejně přes Funnel)? Na stejném portu dnes běží Funnel pro kosync a další služby, změnu je nutné
udělat opatrně.

**Doporučení:** ano, na samostatném portu bez Funnelu.

**Odpověď:**

## P-004 Oprávnění pro noční běh bez dozoru — ☐ čeká

**Otázka:** V noci nikdo nemůže potvrzovat jednotlivé příkazy, které Claude spouští. Jakou míru
samostatnosti mu povolíš?

**Doporučení:** seznam povolených příkazů v `.claude/settings.json` projektu (git, testy, Docker
Compose projektu, codex exec, práce se soubory v `/home/honza/rezie*`). Ne plné vypnutí kontrol
(`--dangerously-skip-permissions`). PM návrh seznamu připraví v F0-B03/B04 a ukáže ti ho
ke schválení.

**Odpověď:**
