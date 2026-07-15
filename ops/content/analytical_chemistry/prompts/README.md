# R3 prompt release: аналитическая химия

Production snapshot снят 2026-07-13 для ассистента `9243941e323d457aa57ec00cd0192a92`. `grader.txt` и `tutor.txt` остаются точными снимками active-версий. `generator.txt` — R3 release candidate на базе generator v3: помимо базового typed-контракта добавлены полные `gravimetry` и `conductometry` facts, запрет скрытых исходных величин и границы детерминированного покрытия. Production ID в таблице фиксирует базовую версию, а не ID ещё не созданного кандидата.

| Роль | Файл | Production prompt ID | Версия | Source | Target family | Notes |
|---|---|---|---:|---|---|---|
| generator | `generator.txt` | base: `e4e6fed23d514cb7b5bdc794d85eafb7` | R3 candidate from 3 | manual | deepseek | `typed gravimetry/conductometry + advanced-model-boundaries; pending create/activation` |
| grader | `grader.txt` | `fe524ab89f6441be8e5cc624391453bb` | 2 | manual | deepseek | `manual-pro-first-2026-07-13: полный ответ, независимая проверка эталона, консервативный OCR-review.` |
| tutor | `tutor.txt` | `1eaec8d41fbf4305baa4c950774540ca` | 3 | manual | deepseek | `manual-v3-free-study-vs-assessment-context-2026-07-13` |

## Выпуск в Studio

Для R3 создать новую версию generator после успешного прогона `../gold-cases-r2.json`; grader/tutor не пересоздавать без отдельного изменения. Имя файла regression suite сохранено для совместимости текущего импортера, его внутренний `content_version` — R3. При аварийном восстановлении любой роли:

1. Прочитать соответствующий `.txt` как UTF-8 и передать его целиком в `system_prompt` запроса `POST /api/assistants/9243941e323d457aa57ec00cd0192a92/prompts`.
2. Указать ту же `role`, `source` будет выставлен сервером как `manual`; обязательно задать `target_family: "deepseek"`. В `notes` указать причину выпуска/восстановления, R3 content version и ссылку на этот repo artifact.
3. Активировать ID созданной версии запросом `POST /api/assistants/9243941e323d457aa57ec00cd0192a92/prompts/{prompt_id}/activate`.
4. Повторно получить список промптов и проверить: у роли ровно одна версия со статусом `active`, `target_family=deepseek`, а `system_prompt` совпадает с файлом. Для generator дополнительно сгенерировать по одному кейсу каждого typed-блюпринта и проверить полный автоматический evidence gate.

Не деактивировать текущую production-версию до успешного создания новой: endpoint активации сам архивирует прежнюю active-версию атомарно в рамках роли.
