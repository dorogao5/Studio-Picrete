# Active prompts: общая и неорганическая химия

Repo snapshot синхронизирован 2026-07-13 с production API `dev.picrete.com` для ассистента `9fbc228df47e4d679c7a49b57d65af59`. Все три роли активны, используют DeepSeek decision-grade policy; файлы содержат точный `system_prompt`.

| Роль | Файл | Production prompt ID | Версия | Source | Target family | Notes |
|---|---|---|---:|---|---|---|
| generator | `generator.txt` | `f7f05920fd4b40868152845e386fbfe1` | 5 | manual | deepseek | `manual-pro-first-lab-safe-2026-07-13: независимая проверка эталона, SOP-gate, полнота всех результатов.` |
| grader | `grader.txt` | `75c9fdc7f5984f2f84ea7ca3f50c80ba` | 3 | manual | deepseek | `manual-pro-first-lab-safe-2026-07-13: независимая проверка эталона, SOP-gate, полнота всех результатов.` |
| tutor | `tutor.txt` | `8048b9ac166f42caba83da779da0b619` | 4 | manual | deepseek | `manual-v4-free-study-vs-assessment-context-and-lab-safety-2026-07-13` |

## Восстановление в Studio

Для каждой роли создать manual-версию через `POST /api/assistants/9fbc228df47e4d679c7a49b57d65af59/prompts` с полным содержимым соответствующего файла и `target_family: "deepseek"`, затем активировать новый ID. Endpoint активации атомарно архивирует прежнюю active-версию; текущую версию не следует деактивировать заранее.
