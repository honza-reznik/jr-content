"""Vygeneruje tabulku registru zápisů pro sekci 4 bloku (bez dat uživatelů)."""
from django.test import override_settings

from apps.jrc_ui.tests.test_write_access import API_WRITE_POLICIES, MCP_WRITE_POLICIES, api_writes, registered_app_urls
from apps.jrc_ui.write_access import DELEGATED_WRITES, WRITE_RULES
from apps.mcp.tools import all_tools

SERVICE_AUDIT = {
    'jrc_production:create': ('client nebo create_posts; aktivní členství', 'jrc_production.services.create_content'),
    'jrc_production:quick_idea': ('client nebo create_posts; aktivní členství', 'jrc_production.services.create_content'),
    'jrc_production:move': ('autor/edit_others_posts pro úpravu; approve_posts a fáze pro rozhodnutí; publish_directly pro plán', 'jrc_production.services.move_content/available_moves'),
    'jrc_production:content_detail': ('text/kanály: create_posts + autorství; datum: publish_directly, role a stav', 'jrc_texts.services.save_caption; jrc_production.services.add/remove_content_channel/set_publication_date'),
    'jrc_production:shotlist_action': ('create_posts + autorství', 'jrc_production.shotlist._lock'),
    'jrc_production:mark_published': ('client nebo publish_directly; nikdy viewer; schválená kopie', 'jrc_production.services.mark_published_manually'),
    'jrc_production:approval_decision': ('approve_posts + správná fáze a strana schvalování', 'jrc_production.services.move_content/request_content_changes'),
    'jrc_production:add_content_comment': ('členství; klient/viewer pouze veřejný komentář', 'approvals.comments'),
    'jrc_production:reply_to_content_comment': ('členství + viditelný rodič; stejné viditelnosti vlákna', 'approvals.comments'),
    'jrc_production:resolve_content_comment': ('approve_posts; nikdy client/viewer', 'approvals.comments.resolve_comment'),
    'jrc_texts:script': ('create_posts + autorství; vlastník rozpracované verze', 'jrc_texts.services.can_edit_text + služby verzí'),
    'jrc_texts:concept': ('create_posts + autorství; vlastník rozpracované verze', 'jrc_texts.services.can_edit_text + služby verzí'),
    'jrc_texts:concept_to_script': ('create_posts + autorství; kontrola verzí', 'jrc_texts.services.copy_concept_to_script'),
    'jrc_video:upload_start': ('create_posts + autorství; povolený kanál', 'jrc_video.uploads.start_upload/can_upload'),
    'jrc_video:upload_part': ('aktuální editor + vlastní rozpracovaný upload', 'jrc_video.uploads._own_version/_require_current_editor'),
    'jrc_video:upload_complete': ('aktuální editor + vlastní rozpracovaný upload', 'jrc_video.uploads._own_version/_require_current_editor'),
    'jrc_video:upload_abort': ('vlastní rozpracovaný upload', 'jrc_video.uploads._own_version'),
    'jrc_video:video_final': ('approve_posts; nikdy client/viewer', 'jrc_video.player.can_mark_final/set_final'),
    'jrc_thumbnails:upload': ('create_posts + autorství', 'jrc_thumbnails.services.can_upload/upload_thumbnail'),
}

DELEGATE_TRANSLATIONS = {
    'allauth session authentication': 'přihlášení vlastního účtu přes allauth',
    'allauth invitation token': 'registrace podle nastavení a platné pozvánky (P1-B24)',
    'authenticated current user': 'přihlášený vlastní účet',
    'authenticated current user (including password recheck)': 'přihlášený vlastní účet; citlivé změny s opětovným ověřením hesla',
    'CSRF protected logout': 'odhlášení vlastní relace s ochranou CSRF',
    'organization administrator; P1-B23': 'správce organizace; P1-B23',
    'authenticated organization member; P1-B23': 'přihlášený člen cílové organizace; P1-B23',
    'invitation token and matching email; P1-B24': 'token pozvánky a odpovídající e-mail; P1-B24',
    'workspace owner/manager; P1-B23': 'vlastník/správce workspace; P1-B23',
    'staff only': 'pouze správce instalace (is_staff)',
    'organization manage_api_keys and grant intersection; P1-B23': 'organizační manage_api_keys + průnik přidělovaných oprávnění; P1-B23',
    'organization manage_api_keys; P1-B23': 'organizační manage_api_keys; P1-B23',
    'organization owner/admin': 'vlastník/správce organizace',
    'notification recipient': 'příjemce upozornění',
    'signed unsubscribe token': 'podepsaný odhlašovací token',
    'signed OAuth state and session workspace': 'podepsaný stav OAuth a workspace z relace',
    'workspace manager (feature off in MVP)': 'správce workspace (funkce v MVP vypnutá)',
    'portal token and allowed workspace posts': 'platná relace portálu a povolené obsahy workspace',
    'one-use portal token confirmed by POST': 'jednorázový token portálu potvrzený přes POST',
    'valid connection token (feature off in MVP)': 'platný token připojení (funkce v MVP vypnutá)',
    'valid connection token and signed OAuth state (feature off in MVP)': 'platný token připojení a podepsaný stav OAuth (funkce v MVP vypnutá)',
    'connection token (feature off in MVP)': 'token připojení (funkce v MVP vypnutá; expirace viz omezení auditu)',
    'internal HMAC signature, replay and time checks': 'vnitřní podpis HMAC; kontrola času a opakování',
    'authenticated active workspace member; CSRF protected SSO ticket': 'přihlášený člen aktivního workspace; lístek SSO s ochranou CSRF',
    'verified webhook signature': 'ověřený podpis příchozí události',
    'OAuth dynamic registration policy and rate limit': 'pravidla dynamické registrace klienta OAuth a omezení četnosti',
    'require_org_permission (billing or use_intelligence)': 'require_org_permission (fakturace nebo use_intelligence; MVP vypnuto)',
    'authenticated owner of pending subscription': 'přihlášený vlastník čekajícího předplatného; MVP vypnuto',
}

with override_settings(JRC_FEATURES={}):
    print('#### Audit webových cest')
    print('\nPředpony cest: `/jrc/`. Zkratka W = `WriteAccessMiddleware` za RBAC. Každé oprávnění v jedné buňce je vyžadováno současně; autorství znamená vlastní obsah nebo `edit_others_posts`. Služby JR navíc ověřují fázi, autora a vlastníka rozpracované verze. P1-B23/P1-B24 jsou explicitní hranice souběžných bloků; jejich soubory zde neměním.\n')
    print('| Cesta (POST) | Potřebné oprávnění | Kde se kontroluje |')
    print('|---|---|---|')
    count = 0
    for key, path, callback in registered_app_urls():
        if key not in WRITE_RULES and key not in DELEGATED_WRITES:
            continue
        count += 1
        if key in WRITE_RULES:
            rule = WRITE_RULES[key]
            permissions = ', '.join(rule.permissions) or 'členství'
            if rule.owned_object:
                permissions += f'; autorství ({rule.owned_object})'
            if rule.domain == 'service':
                permissions, service = SERVICE_AUDIT[key]
            elif rule.domain == 'transition':
                permissions += '; pouze tým; rozhodnutí approve_posts; publikace publish_directly; jinak autorství'
            elif rule.domain == 'team_review':
                permissions += '; týmový schvalovatel'
            elif rule.domain == 'connection_token':
                permissions = 'platný token; neexpirovaný, neodvolaný odkaz; aktivní workspace (MVP vypnuto)'
            where = 'W + ' + callback.__module__ if rule.domain else 'W; objekt v témže workspace'
            if rule.domain == 'service':
                where = 'W (POST) + apps.' + service
            elif rule.domain == 'connection_token':
                where = 'W.enforce_write_rule (token) + tokenová služba připojení'
        else:
            permissions = DELEGATE_TRANSLATIONS[DELEGATED_WRITES[key]]
            where = callback.__module__ + '.' + callback.__name__
        print(f'| `{path}` | {permissions.replace("|", " nebo ")} | {where} |')
    print(f'\nCelkem {count} webových vstupů. Nezaregistrovaný zápis do našich/upstream aplikací končí 403; PUT/PATCH/DELETE na POST webové cestě končí 405. Čtecí pohledy nemají implicitní právo zápisu.\n')
    print('#### Audit REST API')
    print('\nPředpona `/jrc/api/v1`. Každý požadavek navíc ověřuje platný klíč/OAuth, trvající členství, průnik oprávnění vydavatele a klíče, workspace a povolené kanály.\n')
    print('| Cesta | Potřebné oprávnění | Kde se kontroluje |')
    print('|---|---|---|')
    for method, path in api_writes():
        required = ', '.join(API_WRITE_POLICIES[method, path]).replace('|', ' nebo ') or 'autentizace + oprávnění nástroje'
        where = 'apps.api.auth + apps.api.routers (role/objekt/služba)'
        if path in ('/posts/{post_id}', '/posts/{post_id}/schedule', '/posts/{post_id}/cancel'):
            where += '; require_authored_content'
        print(f'| `{method.upper()} {path}` | {required} | {where} |')
    print('\n#### Audit MCP\n')
    print('| Nástroj (POST /jrc/api/v1/mcp/) | Potřebné oprávnění | Kde se kontroluje |')
    print('|---|---|---|')
    for tool in all_tools():
        if tool.read_only:
            continue
        required = ', '.join(MCP_WRITE_POLICIES[tool.name]).replace('|', ' nebo ')
        print(f'| `{tool.name}` | {required} | apps.api.auth + apps.mcp.handlers + služby objektu |')
