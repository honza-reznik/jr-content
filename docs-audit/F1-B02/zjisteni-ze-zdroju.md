# Doplňující kontrola zdrojů

Revize `3829889`, kontrola 21. 10. 2026. Tato zjištění jsou z četby kódu; živé zkoušky
mají vlastní výsledky v auditu KB. Cizí zdroje v `audit/freeframe/` nejsou součástí našeho gitu.

- Porovnání s `stable` / v1.19.0 (`git diff --stat 6a440f1 HEAD`): pouze dva přidané řádky
  v `docs/architecture.md`. Aplikační kód auditované revize odpovídá v1.19.0.
- `apps/api/routers/upload.py`: zahájení nahrávání, podepsání částí, dokončení, pokračování
  a zrušení. `routers/assets.py:429`: `POST /assets/{id}/versions` zahajuje novou verzi.
- `routers/comments.py:453`: `GET /assets/{id}/comments?version_id=…`, POST na stejnou cestu.
  `schemas/comment.py`: `timecode_start` může být prázdné, číselné v sekundách; také koncový
  čas, odpovědi, viditelnost a kresby. Verze komentáře se ověřuje proti videu.
- `models/asset.py`: stavy draft, in_review, approved, rejected, archived. Není to požadovaná
  osmice stavů přípravy obsahu. `due_date` je termín, nikoli samostatné datum publikace.
- `models/project.py`: osobní/týmový projekt, členství owner/editor/reviewer/viewer. Neexistuje
  zvláštní profil klienta s jeho seznamem sociálních kanálů.
- `components/review/video-player.tsx:439`: video a připravená vrstva pro kresby.
  Časový kód je až v transportním panelu od řádku 546. Překryv lze doplnit jako absolutně
  umístěný text v ploše videa, napojený na již existující `currentTime`. Bez nového převodu videa.
  Prověřit celou obrazovku, mobil, čitelnost a souběh s kreslením; odhad 1 S blok s testy.
- `lib/utils.ts:28` má `formatTimecode(seconds, fps = 24)`, přehrávač ho volá bez fps.
  U zdroje 25 fps proto nemusí souhlasit číslo snímku. Pro požadavek „0:17“ stačí sekundy;
  skutečný snímkový kód by měl dostat snímkovou frekvenci souboru. Není v tomto auditu opravováno.
- S3: originály pod `raw/`, výstupy pod `processed/`, odkazy a metadata v PostgreSQL.
  `S3_STORAGE=minio` je i režim pro jiné kompatibilní služby, `s3` znamená přímo AWS.
- `packages/transcoder/ffmpeg_transcoder.py`: výchozí žebřík 1080p/720p/360p, HLS (video
  rozdělené na malé úseky pro plynulé přehrávání), MP4, náhledy. Rozpočet CPU je plánování
  vláken, nikoli pevné omezení kontejneru; skutečné vytížení se musí měřit.
- Struktura: Next.js 14 / React 18 / TypeScript, FastAPI / Python / SQLAlchemy, PostgreSQL,
  Redis / Celery (fronta úloh a její pracovníci), S3, FFmpeg. 29 databázových migrací.
  Jasné členění modely–schémata–služby–HTTP rozhraní; podrobné vysvětlení oprav přímo v kódu.
  Riziko údržby: transcoder má 2 534 řádků, share router 1 669, comments 1 283, upload 979.
  Mazání je převážně označením záznamu, každá cesta musí správně filtrovat smazaná data.
- Backend pytest: většina testů nahrazuje databázi testovacími objekty; existuje i `real_db`
  s transakcí a návratem změn. Samotné zelené jednotkové testy nezaručují průchod nahrávání.
  Frontend má Vitest a testy React komponent. Proto audit doplňuje živé HTTP a prohlížeč.
- `docs/deployment.md:466`: návod pg_dump/obnova a doporučení zálohovat S3. Není to hotový
  systém společných ověřených záloh JR Content. Restart se zachovaným svazkem není obnova zálohy.
- Licence vlastního FreeFrame kódu: MIT, copyright 2026 Techiebutler. Umožňuje úpravy a vlastní
  použití; při předání kopií nebo podstatných částí zachovat licenci a copyright. Bez záruky.
  Licence všech závislostí/FFmpegu a obrazů tím nejsou automaticky vyřešené; úplný licenční audit
  dodávky není součástí bloku. Zdroj: `LICENSE` v auditované revizi.

Aktivita je ověřena přímo veřejným API GitHubu (malá odpověď, žádné nahrávání):
[contributors](https://api.github.com/repos/Techiebutler/freeframe/contributors?per_page=100),
[poslední commity](https://api.github.com/repos/Techiebutler/freeframe/commits?per_page=5).
Snapshot je v `aktivita.json`. Vráceno 21 lidských účtů a Dependabot; žádná další stránka.
Hlavní účet má 432 příspěvků, ostatní lidé dohromady 83, bot 53. Jsou to počty přiřazené
GitHubem účtům, nikoli záruka počtu aktivních udržovatelů. Poslední commit 19. 10. 2026,
předchozí týž den obsahují opravy oprávnění a nové nabídky. Aktivní, ale závislý na jednom autorovi.
