# R3 prompt release: коллоидная химия

Production snapshot снят 2026-07-13 для ассистента `bb3be1b8a8aa46769fa63c270d4ee6a3`. `grader.txt` и `tutor.txt` остаются точными снимками active-версий. `generator.txt` — R3 release candidate на базе generator v4: линейный BET/Smoluchowski/DLVO-контракт дополнен typed-вариантом BET surface area, явной `NA` и проверяемыми переводами площади и базы массы. Production ID ниже — provenance базы, не ID ещё не созданного кандидата.

| Роль | Файл | Production prompt ID | Версия | Source | Target family | Notes |
|---|---|---|---:|---|---|---|
| generator | `generator.txt` | base: `8330cb5e14f94a9598c9bab536ffa561` | R3 candidate from 4 | manual | deepseek | `typed BET linear/surface-area, Smoluchowski and DLVO facts; pending create/activation` |
| grader | `grader.txt` | `38540a81e3854844a1e2eb89c2472f0a` | 2 | manual | deepseek | `manual-pro-first-2026-07-13: полный ответ, независимая проверка эталона, консервативный OCR-review.` |
| tutor | `tutor.txt` | `404d36ac62cf436a9c7434e53035f36d` | 3 | manual | deepseek | `manual-v3-free-study-vs-assessment-context-2026-07-13` |

## Выпуск в Studio

После course binding и успешного прогона `../gold-cases-r2.json` создать новую R3-версию generator; grader/tutor не пересоздавать без отдельного изменения. Имя файла regression suite сохранено для совместимости текущего импортера, его внутренний `content_version` — R3. При аварийном восстановлении любой роли:

1. Прочитать соответствующий `.txt` как UTF-8 и передать его целиком в `system_prompt` запроса `POST /api/assistants/bb3be1b8a8aa46769fa63c270d4ee6a3/prompts`.
2. Указать ту же `role`, `source` будет выставлен сервером как `manual`; обязательно задать `target_family: "deepseek"`. В `notes` указать причину выпуска/восстановления, R3 content version и ссылку на этот repo artifact.
3. Активировать ID созданной версии запросом `POST /api/assistants/bb3be1b8a8aa46769fa63c270d4ee6a3/prompts/{prompt_id}/activate`.
4. Повторно получить список промптов и проверить: у роли ровно одна версия со статусом `active`, `target_family=deepseek`, а `system_prompt` совпадает с файлом. Для generator дополнительно получить `PASS` на каждом typed-блюпринте и ожидаемый block на mutation cases.

Не деактивировать текущую production-версию до успешного создания новой: endpoint активации сам архивирует прежнюю active-версию атомарно в рамках роли.
