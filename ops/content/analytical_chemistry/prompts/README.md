# Active prompts: аналитическая химия

Repo snapshot снят 2026-07-13 с production API `dev.picrete.com` для ассистента `9243941e323d457aa57ec00cd0192a92`. Перед записью у каждой роли подтверждён статус `active`. Файлы содержат точный `system_prompt`; production IDs ниже фиксируют provenance исходных версий и не переиспользуются при создании новой версии.

| Роль | Файл | Production prompt ID | Версия | Source | Target family | Notes |
|---|---|---|---:|---|---|---|
| generator | `generator.txt` | `e4e6fed23d514cb7b5bdc794d85eafb7` | 3 | manual | deepseek | `manual-pro-first-2026-07-13: полный ответ, независимая проверка эталона, консервативный OCR-review.` |
| grader | `grader.txt` | `fe524ab89f6441be8e5cc624391453bb` | 2 | manual | deepseek | `manual-pro-first-2026-07-13: полный ответ, независимая проверка эталона, консервативный OCR-review.` |
| tutor | `tutor.txt` | `b396246ef14a4632b092bdd1ea85029e` | 2 | manual | deepseek | `manual-pro-first-2026-07-13: полный ответ, независимая проверка эталона, консервативный OCR-review.` |

## Восстановление в Studio

Для каждой роли:

1. Прочитать соответствующий `.txt` как UTF-8 и передать его целиком в `system_prompt` запроса `POST /api/assistants/9243941e323d457aa57ec00cd0192a92/prompts`.
2. Указать ту же `role`, `source` будет выставлен сервером как `manual`; обязательно задать `target_family: "deepseek"`. В `notes` указать причину восстановления и ссылку на этот repo snapshot.
3. Активировать ID созданной версии запросом `POST /api/assistants/9243941e323d457aa57ec00cd0192a92/prompts/{prompt_id}/activate`.
4. Повторно получить список промптов и проверить: у роли ровно одна версия со статусом `active`, `target_family=deepseek`, а `system_prompt` совпадает с файлом.

Не деактивировать текущую production-версию до успешного создания новой: endpoint активации сам архивирует прежнюю active-версию атомарно в рамках роли.
