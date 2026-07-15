# Выпуск курируемого химического контента

Выпуск состоит из двух независимых fail-closed фаз. Сначала Studio получает точную версию документа и карточек, затем устанавливаются шаблоны, промпты и сценарий проверки. Обе команды по умолчанию работают без записей.

## Предварительные проверки

```bash
cd /Users/doroga/Documents/projects/rust-picrete/Studio-Picrete
git diff --check
backend/.venv/bin/pytest -q ops/tests
(
  cd backend
  .venv/bin/pytest -q tests/test_chemistry_content_gold_cases.py
  .venv/bin/ruff check app tests ../ops
)
```

Перед `--apply` production-бэкенд должен принимать `source_document_id` в `PATCH /api/assistants/{assistant_id}/sheets/{sheet_id}` и проверять, что документ принадлежит той же дисциплине. Старые документы и карточки команды не удаляют.

## 1. Документы и карточки

```bash
backend/.venv/bin/python ops/sync_curated_sources.py \
  --base-url https://dev.picrete.com \
  --auth-header-file /tmp/picrete-studio-header \
  --dry-run

backend/.venv/bin/python ops/sync_curated_sources.py \
  --base-url https://dev.picrete.com \
  --auth-header-file /tmp/picrete-studio-header \
  --apply

# После успешного применения должно быть planned_mutations=0.
backend/.venv/bin/python ops/sync_curated_sources.py \
  --base-url https://dev.picrete.com \
  --auth-header-file /tmp/picrete-studio-header \
  --dry-run
```

Локальный `grounding.md`, его declared SHA-256 и разобранный production-документ должны совпасть после NFC/LF-нормализации. То же правило действует для каждого `sheets/<slug>.md` и production `content_markdown`; карточка обязана быть canonical/student, иметь точные `kind`, `ord` и быть привязана к документу с точными `title`, `effective_version` и непустым `course_scope`. Любое расхождение останавливает вторую фазу.

Обновление trusted-документа или карточки переводит старые автоматические решения в `needs_review`. Количество затронутых решений и отдельно одобренных задач выводится в dry-run; его нужно сверить до применения.

## 2. Шаблоны, промпты и сценарии

```bash
backend/.venv/bin/python ops/apply_content_release.py \
  --base-url https://dev.picrete.com \
  --auth-header-file /tmp/picrete-studio-header \
  --dry-run

backend/.venv/bin/python ops/apply_content_release.py \
  --base-url https://dev.picrete.com \
  --auth-header-file /tmp/picrete-studio-header \
  --apply

# После успешного применения должно быть planned_mutations=0.
backend/.venv/bin/python ops/apply_content_release.py \
  --base-url https://dev.picrete.com \
  --auth-header-file /tmp/picrete-studio-header \
  --dry-run
```

Статус `certified_theory_and_preflight_calculation_only` допускается только вместе с `operational_procedure_status=blocked_until_approved_protocol_binding`: он не разрешает генерировать лабораторные процедуры из исторического пособия.

Файл авторизации должен иметь права `0600`. После выпуска его следует удалить. Токен нельзя передавать аргументом командной строки или печатать в логах.
