# R2 prompt release: общая и неорганическая химия

Production snapshot синхронизирован 2026-07-13 для ассистента `9fbc228df47e4d679c7a49b57d65af59`. `grader.txt` и `tutor.txt` остаются точными снимками active-версий. `generator.txt` — R2 candidate на базе v5 с typed facts и жёстким исключением повреждённого OCR/vision metadata.

| Роль | Файл | Production prompt ID | Версия | Source | Target family | Notes |
|---|---|---|---:|---|---|---|
| generator | `generator.txt` | base: `f7f05920fd4b40868152845e386fbfe1` | R2 candidate from 5 | manual | deepseek | `typed stoichiometry/dilution facts + OCR truth boundary; pending create/activation` |
| grader | `grader.txt` | `75c9fdc7f5984f2f84ea7ca3f50c80ba` | 3 | manual | deepseek | `manual-pro-first-lab-safe-2026-07-13: независимая проверка эталона, SOP-gate, полнота всех результатов.` |
| tutor | `tutor.txt` | `8048b9ac166f42caba83da779da0b619` | 4 | manual | deepseek | `manual-v4-free-study-vs-assessment-context-and-lab-safety-2026-07-13` |

## Выпуск в Studio

После успешного прогона `../gold-cases-r2.json` создать manual-версию generator через `POST /api/assistants/9fbc228df47e4d679c7a49b57d65af59/prompts` с `target_family: "deepseek"`, затем атомарно активировать новый ID. Grader/tutor не пересоздавать без отдельного изменения. Для аварийного восстановления соответствующий файл передаётся целиком; текущую production-версию не следует деактивировать заранее.
