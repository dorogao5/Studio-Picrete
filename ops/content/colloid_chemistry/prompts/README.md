# Active prompts: коллоидная химия

Repo snapshot снят 2026-07-13 с production API `dev.picrete.com` для ассистента `bb3be1b8a8aa46769fa63c270d4ee6a3`. Перед записью у каждой роли подтверждён статус `active`. Файлы содержат точный `system_prompt`; production IDs ниже фиксируют provenance исходных версий и не переиспользуются при создании новой версии.

| Роль | Файл | Production prompt ID | Версия | Source | Target family | Notes |
|---|---|---|---:|---|---|---|
| generator | `generator.txt` | `8330cb5e14f94a9598c9bab536ffa561` | 4 | manual | deepseek | `manual-v4-self-contained-constants-and-grounded-claims-2026-07-13` |
| grader | `grader.txt` | `38540a81e3854844a1e2eb89c2472f0a` | 2 | manual | deepseek | `manual-pro-first-2026-07-13: полный ответ, независимая проверка эталона, консервативный OCR-review.` |
| tutor | `tutor.txt` | `014d3df0bab64e3c8e44a24bb5585180` | 2 | manual | deepseek | `manual-pro-first-2026-07-13: полный ответ, независимая проверка эталона, консервативный OCR-review.` |

## Восстановление в Studio

Для каждой роли:

1. Прочитать соответствующий `.txt` как UTF-8 и передать его целиком в `system_prompt` запроса `POST /api/assistants/bb3be1b8a8aa46769fa63c270d4ee6a3/prompts`.
2. Указать ту же `role`, `source` будет выставлен сервером как `manual`; обязательно задать `target_family: "deepseek"`. В `notes` указать причину восстановления и ссылку на этот repo snapshot.
3. Активировать ID созданной версии запросом `POST /api/assistants/bb3be1b8a8aa46769fa63c270d4ee6a3/prompts/{prompt_id}/activate`.
4. Повторно получить список промптов и проверить: у роли ровно одна версия со статусом `active`, `target_family=deepseek`, а `system_prompt` совпадает с файлом.

Не деактивировать текущую production-версию до успешного создания новой: endpoint активации сам архивирует прежнюю active-версию атомарно в рамках роли.
