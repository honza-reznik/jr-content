# Lokální knihovny JR Content

Přidáno 2026-10-23, F2-B10. Staženo z registry.npmjs.org přes npm v Dockeru.
Přebrány pouze soubory pro prohlížeč a původní licence. Ze `swagger-ui.css` a
`chart.umd.js` odstraněny jen odkazy `sourceMappingURL` na nepřibalené vývojové mapy;
jinak obsah beze změn. Odkazy bránily sestavení statických souborů z čistého checkoutu.

- `flatpickr@4.6.13`; původ a licence v přiloženém souboru LICENSE.
- `chart.js@4.4.6`; původ a licence v přiloženém souboru LICENSE.
- `sortablejs@1.15.6`; původ a licence v přiloženém souboru LICENSE.
- `swagger-ui-dist@5.17.14`; původ a licence v přiloženém souboru LICENSE.

Kontrolní součty SHA-256:

```text
41a84aa2caba645f966a18d9c2056b73e6d3a81d80bc0046bc0011a2634d4cce  chart.js/LICENSE.md
cf5c4d953245f661d81a5dac63575f64d8a12b8ccf42cde10829c0786b9722bf  chart.js/chart.umd.js
5fd57afc3905651175947ae44818e91ffcf5b605e4c88fac42d9700243bd9095  flatpickr/LICENSE.md
1b34a42552c96f10e4dfaaa4a367276b03868aacff63c1ac42ffe331352bc754  flatpickr/flatpickr.min.css
1eeab1cb779471a0b0aaa93dd91c2eb1aa537d696f01ab05ea9dabc55e8525a1  flatpickr/flatpickr.min.js
e94dfc31e800d169257569db270457c9f028440c9ccae41e7eb78b2db18f1298  sortablejs/LICENSE
6d0a831fc19b4bae851797ad3393157e861afb7862459c11226359b27e2c4337  sortablejs/Sortable.min.js
cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30  swagger-ui-dist/LICENSE
0d20d1adef18aee3f40dd258172155521ce702ac445cb5f7b7d60ed32dad2fb2  swagger-ui-dist/NOTICE
c2e4a9ef08144839ff47c14202063ecfe4e59e70a4e7154a26bd50d880c88ba1  swagger-ui-dist/swagger-ui-bundle.js
0a1cb8b082687b16728122913354c52909a36582b449b1b54ff7a96e0477dc1e  swagger-ui-dist/swagger-ui.css
```
