# Studio-Picrete v3: студия «цифрового двойника преподавателя»

Цель: преподаватель загружает материалы курса (РПД, конспект, учебники, задачник, справочные таблицы),
и на их основе строит ИИ-ассистента, который генерирует задачи, проверяет работы и объясняет студентам
**в терминологии и на данных именно этого курса** (например, значения энергии Гиббса — только из таблиц курса).

Запуск в сентябре: Неорганическая, Аналитическая, Коллоидная химия (+ возможно Физхимия на физтехе).

## Столпы

1. **База знаний (KB)** — документы курса → markdown (Datalab marker) → чанки → FTS-поиск (SQLite FTS5 + RU-стемминг).
2. **Справочные материалы (ReferenceSheet)** — канонические данные курса (таблицы величин, глоссарий, обозначения,
   формулы). Инжектятся ВЕРБАТИМ в промпты генерации/проверки/разбора с инструкцией «использовать ТОЛЬКО эти данные».
3. **Блюпринты задач (расширенный TaskTemplate)** — типовая задача: вид, формат ответа, допуск, привязанные
   справочники, примеры из задачника, флаги валидации.
4. **Генерация с автовалидацией (GenerationBatch)** — фоновая партия: генерация → независимый решатель (другая
   модель решает вслепую и сравниваем ответы) → сверка чисел условия со справочниками → sanity-чеки → дедуп.
   Статусы задач: draft → validated | needs_review → approved | rejected.
5. **Режим «Разбор» (tutor)** — преподаватель играет роль студента в чате: как ассистент объяснит ошибку,
   в тех ли терминах, на тех ли данных. Роль промпта "tutor" (третья, рядом с grader/generator).
6. **Прозрачность** — «Что видит модель»: превью финального собранного промпта (system + user + заземление).
7. **Экспорт в прод** — approved-задачи → JSON формата банка задач Picrete
   (`[{paragraph, topic, theory_text, tasks:[{number,text,images,answer}]}]`) и формат вариантов экзамена
   (`{content, reference_solution, reference_answer, answer_tolerance}`).

## Модели данных (backend/app/models.py — УЖЕ НАПИСАНО, не менять)

Новые: KnowledgeDocument, KnowledgeChunk, ReferenceSheet, GenerationBatch, TutorRun.
Расширены: TaskTemplate (+task_kind, answer_format, numeric_tolerance_pct, reference_sheet_ids,
example_tasks, kb_query, validation_solver, validation_data_check), GeneratedTask (+batch_id, answer,
status, validation, grounding). PromptVersion.role теперь grader|generator|tutor (валидация в schemas).

GeneratedTask.status: draft | validated | needs_review | approved | rejected.
`approved` (bool) поддерживается в синхроне со status для обратной совместимости.

## Файловая собственность (для параллельных агентов)

- **A1 (KB)**: `services/kb.py`, `services/grounding.py`, `api/kb.py`
- **A2 (Gen)**: `services/taskgen.py` (переписать), `services/validation.py`, `services/export.py`, `api/tasks.py` (переписать)
- **A3 (Tutor/Grounded grading)**: `services/tutor.py`, `api/tutor.py`, `api/preview.py`, `services/meta_prompt.py` (+tutor
  role, +правила заземления), `services/grading.py` (+grounding), `api/playground.py` (+grounding в compare),
  `services/pipeline.py` (+grounding в grade-шаги)
- **A4 (FE Материалы)**: `pages/assistant/MaterialsTab.tsx` (+подкомпоненты в том же файле или `pages/assistant/materials/`)
- **A5 (FE Задачи)**: `pages/assistant/TasksTab.tsx` (переписать)
- **A6 (FE Разбор+Промпты)**: `pages/Playground.tsx` (режим «Разбор»), `pages/assistant/PromptsTab.tsx` (tutor-роль, превью промпта)

Общие файлы (models.py, schemas.py, config.py, contracts.py, main.py, db.py, pyproject.toml,
frontend: types.ts, api.ts, AssistantDetail.tsx) — уже готовы, агентам НЕ менять.
Стубы `api/kb.py`, `api/tutor.py`, `api/preview.py`, `MaterialsTab.tsx` — заменить содержимое целиком.

## Ключевые интерфейсы (обязательные сигнатуры)

### services/grounding.py (A1; используют A2 и A3)

```python
async def build_grounding_block(
    db: AsyncSession,
    assistant_id: str,
    *,
    sheet_ids: list[str] | None = None,   # None => все is_canonical листы ассистента
    query: str = "",                       # если непусто и include_kb — FTS-поиск чанков
    kb_limit: int = 6,
    include_kb: bool = True,
    max_chars: int = 24000,
) -> str
```
Возвращает markdown-блок (или "" если нечего включать):
```
## СПРАВОЧНЫЕ МАТЕРИАЛЫ КУРСА
Используйте ТОЛЬКО эти данные и обозначения. Если необходимых данных здесь нет — явно скажите об этом,
не подставляйте значения из общих знаний.

### <sheet.title> (<kind label>)
<content_markdown>
...
## ВЫДЕРЖКИ ИЗ МАТЕРИАЛОВ КУРСА (контекст, терминология)
### <doc.title> — <chunk.heading>
<content>
```
Листы режутся по max_chars (целиком лист за листом, приоритет по ord), чанки добавляются в остаток бюджета.

### services/kb.py (A1)

- `async def ingest_document(document_id: str) -> None` — фоновая задача (создаёт СВОЮ сессию через SessionLocal):
  pdf/jpg/png/webp → Datalab marker (переиспользовать `services/ocr.run_datalab_ocr`, но с
  `max_poll_attempts=settings.datalab_kb_max_poll_attempts`); .md/.txt/.markdown → читать как текст.
  Затем: split → chunks → FTS-индексация. Статусы документа: uploaded → parsing → parsed | failed (+error).
- `def split_markdown(md: str) -> list[dict]` — сплит по заголовкам (#, ##, ###), длинные секции дорезать по
  абзацам до ~2500 символов, таблицы (строки, начинающиеся с |) не разрывать и помечать kind="table";
  каждый чанк: {heading: "H1 › H2", content, kind, char_len}.
- `async def ensure_fts(conn) -> None` — CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(chunk_id UNINDEXED, content_norm) — вызывается из main.lifespan (уже подключено).
- `def normalize_ru(text: str) -> str` — lowercase, ё→е, токенизация \w+, snowballstemmer("russian") по токену.
- `async def index_chunks(db, chunks) / deindex_document(db, document_id)` — raw SQL INSERT/DELETE в kb_fts.
- `async def search_chunks(db, assistant_id, query, limit=8) -> list[KnowledgeChunk]` — normalize_ru(query),
  токены через " OR ", `SELECT chunk_id FROM kb_fts WHERE kb_fts MATCH :q ORDER BY bm25(kb_fts) LIMIT :n`,
  затем фильтр по assistant_id. Пустой запрос/ошибка MATCH → [].
- `async def extract_syllabus(db, document, provider, model) -> list[str]` — LLM (архитектор через
  resolve_architect из api.assistants) читает markdown РПД (до 60000 симв.) → JSON {"topics": ["..."]}.
  Промпт: извлечь разделы/темы курса с их содержанием, кратко, 5–30 тем, на русском.

### api/kb.py (A1) — router = APIRouter(prefix="/api"-less; как в других: APIRouter(tags=["kb"]) с путями от /assistants)

- POST `/assistants/{assistant_id}/kb/documents` — multipart (file, title=Form(""), doc_type=Form("other")),
  лимит settings.kb_max_file_mb, сохранить в settings.kb_dir/{uuid}{suffix}, создать KnowledgeDocument
  (status=uploaded), BackgroundTasks.add_task(ingest_document, doc.id) → KnowledgeDocumentOut.
  doc_type: rpd|notes|textbook|problem_book|reference|methodical|other.
- GET `/assistants/{assistant_id}/kb/documents` → list[KnowledgeDocumentOut] (без markdown, есть chunk_count).
- GET `/assistants/{assistant_id}/kb/documents/{document_id}` → KnowledgeDocumentDetailOut (с markdown).
- POST `.../documents/{document_id}/reparse` — сброс чанков и повторный ingest.
- DELETE `.../documents/{document_id}` — deindex + удалить файл с диска.
- GET `/assistants/{assistant_id}/kb/search?q=&limit=` → list[KnowledgeChunkOut].
- POST `/assistants/{assistant_id}/kb/extract-syllabus` — {document_id} → {"topics": [...]} (НЕ сохраняет в профиль).
- GET `/assistants/{assistant_id}/sheets` → list[ReferenceSheetOut] (ordered by ord, created_at).
- POST `/assistants/{assistant_id}/sheets` — ReferenceSheetCreate.
- PATCH/DELETE `/assistants/{assistant_id}/sheets/{sheet_id}`.
- POST `/assistants/{assistant_id}/sheets/from-chunks` — {document_id, chunk_ids:[...], title, kind} —
  склеить content выбранных чанков в content_markdown.

### services/taskgen.py + validation.py + api/tasks.py (A2)

Генерация v2 (сохранить старый эндпоинт POST /tasks/generate работающим — теперь он тоже проходит через
новый билд промпта, но синхронно и без валидации; главный путь — партии):

- POST `/assistants/{assistant_id}/tasks/batches` — GenerationBatchRequest {template_id?, model_entry_id,
  solver_model_entry_id?, prompt_version_id?, topic, difficulty, count 1..20, instructions, temperature=0.7,
  validate: bool = true} → GenerationBatchOut (status=running); BackgroundTasks запускает run_batch(batch_id, ...).
- GET `/assistants/{assistant_id}/tasks/batches?limit=10` → list[GenerationBatchOut] (recent first).
- GET `/assistants/{assistant_id}/tasks/batches/{batch_id}` → GenerationBatchOut (для поллинга; progress —
  JSON {stage: str, done: int, total: int}).
- run_batch: собственная сессия (SessionLocal). Этапы: build prompt (blueprint + grounding_block(sheet_ids
  из шаблона, query=kb_query или topic) + example_tasks + last-8 dedup) → LLM → парс задач (+answer!) →
  создать GeneratedTask (status=draft, batch_id) → если validate: по каждой задаче validation-этапы,
  обновляя batch.progress после каждой задачи. Ошибка партии → batch.status=failed, error.
- Валидация (services/validation.py):
  - `async def solver_check(provider, model, statement, grounding, answer_format) -> dict`
    — решатель получает ТОЛЬКО условие + справочные материалы, JSON {"solution": "...", "answer": "..."}.
  - `def compare_answers(reference: str, solver: str, tolerance_pct: float) -> dict` — извлечь числа
    (поддержать "1,5·10^3", "1.5e3", "-0,25", проценты, юникод-минус); relative diff ≤ tolerance → match.
    Оба без чисел → нормализованное текстовое сравнение (casefold, strip). Вердикты: match|mismatch|uncertain.
  - `def data_check(statement: str, sheets_text: str) -> dict` — числа условия (с десятичной частью или
    ≥3 значащих цифр; игнорировать целые <100 — стехиометрия/номера) ищутся в тексте справочников
    (нормализация , → .); → {"unknown_numbers": [...], "status": "ok|warn"}.
  - `def sanity_check(task: dict) -> dict` — statement ≥ 30 симв.; rubric непустая и сумма max_score
    критериев == max_score (±0.01); для numeric answer_format ответ непуст. → {"issues": [...]}.
  - `def dedup_check(statement, existing_statements) -> dict` — Jaccard по токен-сетам > 0.65 → duplicate warn.
  - Итог: validation JSON {"solver": {...}, "data": {...}, "sanity": {...}, "dedup": {...},
    "verdict": "validated|needs_review", "reasons": [строки на русском]} → task.status, approved не трогаем.
  - solver mismatch/uncertain, unknown_numbers непуст, sanity issues, duplicate → needs_review с причинами.
- POST `/assistants/{assistant_id}/tasks/{task_id}/revalidate` — {solver_model_entry_id?} пере-валидация одной задачи (синхронно).
- PATCH задача: + status в GeneratedTaskUpdate (approve/reject через него); approved bool синхронизировать.
- POST `/assistants/{assistant_id}/tasks/export` — TaskExportRequest {task_ids: [] | пусто=все approved,
  mode: "bank"|"variants", source_code="studio", source_title=assistant.discipline, version="1.0"} →
  mode=bank: {"source": {...}, "paragraphs": [{paragraph:"1", topic, theory_text:"", tasks:[{number:"1.1",
  text: statement, images: [], answer}]}]} (группировка по topic, нумерация по порядку);
  mode=variants: {"tasks": [{title: topic, content: statement, reference_solution, reference_answer: answer,
  answer_tolerance: tolerance_pct/100 * |answer| если число, иначе 0, max_score, rubric}]}.
  (services/export.py; ответ = JSON-объект, фронт скачивает файлом.)

### services/tutor.py + api/tutor.py + api/preview.py (A3)

- Роль "tutor": FALLBACK_TUTOR_PROMPT (методичный доброжелательный преподаватель дисциплины {discipline};
  разбирает решение/вопрос студента ПОШАГОВО до основ; строго в терминологии и обозначениях курса;
  использует ТОЛЬКО справочные данные курса; не решает за студента новую задачу целиком — ведёт к пониманию;
  markdown + LaTeX $...$). meta_prompt.py: ROLE_BLOCKS + "tutor" (входы: задача, эталон, сообщение/решение
  студента, справочные материалы; выход — markdown-объяснение, БЕЗ JSON) + generate_system_prompt должен
  принимать role="tutor" (contract не подставляется). В build_meta_prompt добавить (для всех ролей) правило:
  «Промпт обязан требовать использовать исключительно справочные данные и терминологию курса из
  пользовательского сообщения; при нехватке данных — явно сообщать, а не брать общесправочные значения».
- PromptVersion role pattern уже "^(grader|generator|tutor)$" в schemas (готово).
- POST `/assistants/{assistant_id}/tutor/chat` — TutorChatRequest {run_id?, task_id?, prompt_version_id?,
  model_entry_id, student_work="", messages: [{role: "user"|"assistant", content}] (история + новое посл.
  сообщение user)} → TutorChatResponse {run: TutorRunOut, reply: str}.
  Систем-промпт: явная версия | активный tutor | FALLBACK. Контекст в первое user-сообщение:
  задача+эталон (если task_id), решение студента, grounding_block(query=текст задачи или вопроса).
  Мультитёрн: messages как есть в chat-completions (llm.chat принять list — там уже user_content: str|list?
  НЕТ: сигнатура chat(system_prompt, user_content) — для мультитёрна добавить в services/tutor.py свой
  httpx-вызов НЕ НАДО; вместо этого склеивать историю в одно user-сообщение с метками «Студент:»/«Ассистент:»
  — проще и совместимо со всеми семействами; reply — text).
  Сохранение: TutorRun.messages = вся история [{role, content}], student_work, обновлять updated_at.
- GET `/assistants/{assistant_id}/tutor/runs?limit=20` → list[TutorRunOut].
- POST `/tutor/runs/{run_id}/feedback` — {rating? 1..5, comment?} → TutorRunOut.
- api/preview.py: POST `/assistants/{assistant_id}/prompt-preview` — PromptPreviewRequest {role, prompt_version_id?,
  task_id?, template_id?, ocr_text="(решение студента)", student_work=""} → {system_prompt, user_message}.
  Собирает РОВНО то, что уйдёт модели (для grader — build_grading_user_message + grounding; для generator —
  build_generation_user_message v2; для tutor — tutor user message). Без вызова LLM.
- grading.py: `build_grading_user_message(..., grounding: str = "")` — блок «Справочные материалы курса»
  перед OCR; run_grading прокидывает grounding. api/playground.py compare: CompareRequest.include_reference
  (bool, default True, уже в схеме) → grounding_block(query=task_text[:200]). services/pipeline.py grade-шаг:
  то же самое от input.task_text.

### Frontend (A4/A5/A6)

Существующие компоненты: `components/ui.tsx` (Button, Input, Textarea, Select, Field, Card, Badge, Tabs,
Modal, Spinner, EmptyState, ErrorNote). Стиль «лабораторный журнал»: перенимать по образцу соседних вкладок.
lucide-react для иконок. Все копирайты на русском. Ошибки — apiErrorMessage. НЕ добавлять новые npm-зависимости.

- **A4 MaterialsTab** (`?tab=materials`, props {assistant, providers}): две секции.
  «Документы курса»: загрузка (input file + select типа: РПД/Конспект/Учебник/Задачник/Справочник/Методичка/Другое),
  список: title, тип-Badge, статус-Badge (обрабатывается⏳/готов/ошибка) с поллингом каждые 3с пока есть parsing,
  chunk_count, размер; действия: просмотр markdown (Modal с <pre>), переразобрать, удалить, «Извлечь темы» (для
  любого parsed дока) → модал со списком чекбоксов тем → «Добавить в профиль» (assistantsApi.update topics:
  merge уникально). «Справочные материалы» (канон данных): карточки по kind (Таблица данных/Глоссарий/
  Обозначения/Формулы/Другое), CRUD-модал (title, kind, description, content_markdown в Textarea mono),
  бейдж «канонический» (is_canonical), создание из чанков дока (модал: выбор parsed-дока → список table-чанков
  с чекбоксами → title+kind → создать). Подсказка вверху: «Эти материалы ИИ обязан использовать вместо
  общесправочных данных».
- **A5 TasksTab** (переписать): три блока.
  1) «Типовые задачи (блюпринты)»: карточки шаблонов с расширенным модал-редактором: name, topic (datalist из
  assistant.topics), task_kind (Расчётная/Теоретическая/Тест В-Н/Тест выбор/Вывод формулы), difficulty,
  answer_format (Число/Формула/Текст/Выбор), numeric_tolerance_pct, instructions, kb_query, reference_sheet_ids
  (мультиселект чекбоксами из sheetsApi.list), example_tasks (список {statement, solution, answer} с
  добавлением/удалением), validation_solver + validation_data_check чекбоксы. Кнопка «Сгенерировать партию».
  2) «Партии генерации»: модал запуска (модель, решатель (по умолчанию другая модель — подсказать), count,
  temperature, validate), список последних партий с прогрессом (поллинг 2.5с пока running: progress.stage,
  done/total), статус-бейджи.
  3) «Банк задач»: фильтр-чипы по статусу (Все/Черновики/Прошли проверку/Требуют внимания/Одобрены/Отклонены),
  карточки: statement (свёрнуто), развернуть → reference_solution, answer, rubric таблицей, validation-отчёт
  (бейджи: «Решатель: совпал/расходится (X vs Y)/не уверен», «Данные: ок / неизвестные числа: …», «Sanity: …»,
  «Дубликат?»), причины needs_review списком, grounding (какие справочники использованы). Действия: Одобрить/
  Отклонить/Перепроверить/Удалить/Редактировать (statement, solution, answer, max_score). Массовое «Одобрить все
  прошедшие проверку». Кнопка «Экспорт в Picrete» → модал (mode: Банк задач/Варианты экзамена, source_code,
  title, version) → скачать JSON (Blob + a.download).
- **A6**: Playground.tsx — третий режим «Разбор» (рядом с существующими режимами Сравнение/Пайплайн; найти
  switcher в файле): выбор дисциплины (общий), выбор задачи из банка (approved/validated первыми) или ручной
  ввод условия, поле «Решение/вопрос студента», выбор модели (один селект), опц. tutor-prompt версия; чат:
  сообщения (пузыри), отправка → tutorApi.chat (run_id из состояния), Markdown НЕ рендерить — <pre> как всюду;
  рейтинг 1-5 + кнопка «В нюанс» (assistantsApi.addNuance с текстом из поля). PromptsTab: роль tutor в
  селекторах (создание вручную и через архитектора), у КАЖДОЙ версии кнопка «Что видит модель» → модал:
  previewApi.preview({role, prompt_version_id}) → показать system_prompt и user_message в <pre> с
  подзаголовками. Бейдж роли: Проверка/Генерация/Разбор.

## Тест-план (интеграция, после агентов)

1. Бэкенд импортируется, uvicorn стартует, create_all+FTS ok.
2. Смоук через httpx TestClient-подобный сценарий (скрипт scripts/smoke.py): login → create assistant →
   upload .md документ (без Datalab) → parsed, чанки, поиск → создать sheet → шаблон → партия с
   validate=false (мок-модели нет — пропускаем LLM-часть? для смоука LLM использовать нельзя) —
   partial: только не-LLM пути. LLM-пути проверяются вручную на dev с реальными ключами.
3. tsc --noEmit + vite build.
4. Деплой: docker prune на сервере, rsync, up --build, смоук на проде с реальным DeepSeek.
