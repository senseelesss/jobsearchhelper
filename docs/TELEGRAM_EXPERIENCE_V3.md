# Job Radar v3 — звіт першого етапу

Дата: 2026-11-13, Europe/Vienna. Це **часткова реалізація завдання v3**:
перевірений інкремент Telegram Reliability із виправленням навігації.
Candidate Intelligence та Mini App у цьому інкременті **не реалізовані**.
Готовність усього v3 або production deployment не заявляється.

## A. Реалізація та безпека робочого дерева

Після перевірки трьох worktree та `fetch origin integration/job-radar-v2-rc`
створено `job-radar-v3`, гілка `feat/telegram-experience-v3`, від `5711ed9`.
RC містить обидві попередні feature-гілки. Знайдену чужу зміну `.env.example`
у collection worktree не редагували. Основну БД, приватний профіль, `.env`,
main і RC не змінювали. Всі тести/benchmarks використовують тимчасові БД.

Реалізовано:

- `answerCallbackQuery` перед будь-якими приватними читаннями/очищенням SQLite;
  однаковий шлях для нових, прострочених і legacy callback. Авторизація — числовий
  owner ID та його private chat, без доступу до БД.
- Незалежні polling/ACK та послідовна черга обробників. Один DB executor зберігає
  SQLite thread affinity; чотири ACK threads не чекають повільного edit/handler.
  Черга обмежена 100 updates, тимчасовий batch — лімітом Telegram getUpdates.
- Telegram отримує лише **durably committed offset**, а не номер останнього
  отриманого/поставленого в чергу update. Інакше crash міг би втратити дію.
  Повтори в поточному процесі пропускаються; restart спирається на збережений offset.
  Помилка запису offset зупиняє poller: він не підтверджує незбережені updates.
- Timeout ACK — 3 секунди; невдалий ACK не скасовує дозволену дію.
- Housekeeping sidecar — максимум раз на 60 секунд; перевірка TTL callback
  залишається при кожному resolve. Жодного кешування станів заявок не додано.
- HTTP 429 має окремий тип із `retry_after`, спільний cooldown transport та
  polling backoff. Відхилений через rate limit edit не запускає replacement send.
  Підтверджена недоставка лишається відхиленням для існуючого outbox; невизначені
  мережеві помилки не отримують blind resend.
- Legacy callback після раннього ACK усе ще показує відсутню вакансію/лист.
- Вимірювання з обмеженими 512 samples на етап, p50/p95 та DEBUG-логами тільки
  назви етапу й тривалості. Немає SQL-тексту, callback ID, URL, профілю чи payload.
- `worker_startup_ms` у стані пошуку: час від queued до запуску supervisor.
  Collection, enrichment, scoring, budgets, application ledger і subprocess
  gateway лишаються існуючими спільними сервісами.

Календарну помилку baseline відтворено: 415 passed, 1 failed. Фікстура backlog
порівнювала поточну дату з фіксованими днями листопада 2026. Окремий коміт
`d3ec0f2` задає фіксований вік вакансій відносно цих днів, не змінює production logic.
Перший запуск suite із батьківського каталогу був некоректним через відносні шляхи
фікстур; наведений baseline — повторний запуск із кореня RC.

## B. Продуктивність

Однакова машина/interpreter, 1 000 синтетичних вакансій, 40 samples на сценарій,
справжні тимчасові SQLite main/sidecar, підмінений Telegram transport. Значення
не характеризують інтернет, реальний Telegram чи файлову систему production БД.

| Сценарій / метрика | RC до | v3 після |
|---|---:|---:|
| Кешоване Home: ACK p50 / p95, мс | 0,011 / 0,019 | 0,002 / 0,003 |
| Кешоване Home: відповідь p50 / p95, мс | 0,301 / 0,370 | 0,295 / 0,340 |
| Кешоване Home: медіана SQL statements | 33 | 28 |
| Housekeeping із штучними 50 мс: ACK p50 / p95, мс | 55,051 / 55,255 | 0,012 / 0,020 |
| Той самий повільний сценарій: відповідь p50 / p95, мс | 55,486 / 57,030 | 56,111 / 57,486 |
| Перше Home, один замір, мс | 275,653 | 193,711 |
| Перше Home: SQL statements | 8 057 | 8 057 |

Основний доведений ефект — ACK перестав чекати обслуговування БД. Час повної
відповіді за заблокованої БД не покращився. Різниця одноразового cold Home не є
доказом оптимізації індексації: кількість SQL не змінилася. Початковий індекс
і далі має N+1-подібну hydration; це відкрита задача.

Телеметрія: `callback_ack_dispatch`, `callback_ack_complete`, `callback_ack_api`,
`handler_queue_wait`, `handler_total`, `callback_route`, `first_response_ready`,
`message_delivery`, `telegram_api_<method>`, `query_detail/sync/statistics/snapshot/page`,
`search_dispatch`. Query timing включає SQL **і** Python hydration; це не точний
час окремого SQL. First-response-ready означає готовність перед відправкою,
а не появу на екрані телефону. Handler/route включають вкладені витрати — їх не
можна додавати. `getUpdates` включає long-poll очікування. Час від натискання на
телефоні до отримання callback невідомий: старий `message.date` його не вимірює.

`telemetry.snapshot()` доступний усередині процесу; samples не переживають restart.
Немає нового публічного metrics endpoint. End-to-end Telegram p95 live не виміряно.

## C. Telegram UX і інвентар навігації

Виправлено Back: значення фільтра → категорії → **та сама snapshot/page**.
Clear All зберігає sort і межі конкретного запуску пошуку. До виправлення Back
вів одразу на Home, а Clear губив `since`. Покрито всі три локалі.

| Екран | Дії |
|---|---|
| Home | Search, Recommendations, Saved, Applications, Statistics, Settings, All, default list |
| Список | Details, Previous/Next, Filters, 3 sorts, New/Recommendations/Saved/Applied/Skipped/All |
| Vacancy detail | Original URL, Save, Applied, Skip, інші lifecycle states, AI availability, stored draft, Back/Previous/Next |
| Filters | Категорія, значення, Apply через вибір, Clear, Back до контексту |
| Settings | uk/de/en, notifications on/off, default list, search profiles |
| Search | profile/source selection, depth, Quick Search, Full Search quote/confirmation, status refresh |
| Run | статус, реально збережені counters/stages, нові результати, recommendations/search |
| Statistics | справжні counts, sources/priorities/states, outbox, recent runs |
| Допоміжні | Help, expired/unavailable/error, відсутній draft, недоступний individual AI |

Новий обхід generated buttons перевіряє 22 типи action у uk/de/en, resolvable tokens,
64-byte callback limit, непорожній текст та public URLs. Існуючі тести перевіряють
усі lifecycle states, revisions, pagination, Unicode, TTL, missing data і templates.
Обхід типів дій не доводить усі можливі комбінації фільтрів чи live працездатність URL.
Новий семантичний UX фільтрів, presets і dashboard Milestone B ще не реалізовані.
Старі валідні navigation tokens можуть повторно відкрити старий екран; screen-generation
invalidation ще немає. Захист статусів optimistic revision/request ID збережено.

## D. Candidate Profile

Нову модель/editor/provenance не додано. Існуючий canonical YAML та `candidate.py`
збережено; scoring не переведено на іншу копію профілю. Наступний етап має додати
versioned model і атомарне редагування того самого effective profile з CAS, validation,
provenance, Save/Cancel та preview. Не можна створювати окрему Telegram DB-копію істини.

## E. Matching

Scoring algorithm, hard eligibility, profile/content/prompt/model cache identity
та бюджети не змінені. Збережені оцінки і пояснення доступні старими шляхами.
Індивідуальний paid AI не реалізований; UI чесно повідомляє обмеження.
Full Search залишається існуючим confirmed/budgeted gateway. Редагування профілю
не може спричинити автоматичну оплату — самого редактора ще немає.

## F. Mini App

Backend API, frontend, Telegram initData validation та HTTPS staging **відсутні**.
Не додано декоративної Open Mini App кнопки чи фальшивих даних. Frontend build,
typecheck, browser E2E та live відкриття Mini App не виконувалися: немає реалізації.
Це відкладена робота, не блокування через відсутній URL: її можна реалізувати локально.
Публічний HTTPS/BotFather потрібні лише для наступної live-перевірки.

## G. Перевірки

Повний suite: **434 passed**; Ruff: **All checks passed**; `git diff --check`: clean.
Включає існуючий detached gateway → реальний CLI/pipeline з підміненими providers,
restart persistence, budget reservations, localized outbox і нові regression tests.
Новий runtime test блокує перший handler, перевіряє ACK другого, порядок commit,
відсутність раннього server offset і dedupe. Окремо перевірено аварію durable write,
ACK timeout configuration, ACK failure, 429 cooldown, Back і 22 action types.
Жодних paid calls, live Telegram messages чи нових міграцій БД.

## H. Git

Гілка: `feat/telegram-experience-v3`; база/PR target: `integration/job-radar-v2-rc`.
Коміти: окрема календарна фікстура; reliability/navigation із regression tests;
benchmark і цей звіт. PR має бути draft: він не є завершеним v3. Не merge і не deploy.
Push/PR URL та точні фінальні SHA повідомляються в чаті після виконання Git-операцій.

## I. Залишок завдання

**Виконано й перевірено:** описаний reliability/navigation інкремент, offline metrics,
регресії та окрема deterministic fixture.

**Реалізовано, live не перевірено:** незалежний polling/ACK, transport cooldown,
поведінка меню на реальному Telegram, worker-startup metric на production host.

**Частково:** Milestone A — немає screen-generation захисту навігації, повного
SQL/render breakdown, cold-index оптимізації, live latency та всіх load scenarios.

**Не реалізовано, не заблоковано:**

1. B: компактний dashboard/cards, bot presets; розділення vacancy/assessment/workflow;
   notification preferences, quiet hours/timezone/digest/quality thresholds.
2. C: versioned Candidate MetaCard і section editor; normalized vacancy provenance;
   canonical scoring integration, stale-profile refresh, explainability, feedback store/reports.
3. D: authenticated versioned API для dashboard/jobs/status/profile/search/history/AI/settings/stats;
   server-side initData HMAC/freshness/allowlist, authorization кожного endpoint, validation,
   rate limits, CORS/HTTPS; mobile frontend усіх 8 views з темами, native BackButton,
   polling, rollback і спільним application ledger.
4. E: повний Telegram → collection → Mini App → profile → mocked scoring → restart
   acceptance, auth/security tests, frontend typecheck/build/browser E2E, migration copies,
   performance review та остаточний runbook.

**Live gates:** потрібні приватні валідні credentials і підтверджена історична rotation
(успадкована умова RC), staging HTTPS/BotFather для майбутнього Mini App. Нічого
не розгорнуто; відсутність staging не є причиною незавершеної локальної реалізації.

## J. Безпечна ручна перевірка

Для локального macOS checkout, без authoritative DB, мережі чи витрат:

```bash
cd '/Users/senseelesss/Documents/AI assisted job search speedrun/job-radar-v3'
export PYTHONPATH="$PWD/src"
export PYTHONPYCACHEPREFIX=/tmp/job-radar-v3-pycache
JR_PYTHON="$HOME/.cache/job-radar/venv/bin/python"
"$JR_PYTHON" -m pytest -q -p no:cacheprovider
"$JR_PYTHON" -m ruff check .
"$JR_PYTHON" scripts/benchmark_telegram.py --source ../job-radar-v2-rc
"$JR_PYTHON" scripts/benchmark_telegram.py
"$JR_PYTHON" -m job_radar.control.demo --scenario success --walkthrough
"$JR_PYTHON" -m job_radar.control.demo --scenario partial --walkthrough
```

Для ручного bot acceptance після приватного налаштування **окремого тестового бота**,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID`, `TELEGRAM_CHAT_ID` і відсутності іншого poller:

```bash
export JOB_RADAR_CANDIDATE_PROFILE="$PWD/candidate_profile.example.yaml"
./bin/job-radar telegram-poll --database data/v3-acceptance.db \
  --searches searches.example.yaml --output outputs/v3-acceptance \
  --max-pages 1 --search-limit 5 --search-timeout 180 --cooldown 300
```

Це **мережевий opt-in**, не виконаний під час розробки. `/menu` → Search → Quick
Search запускає реальний collection без AI; Refresh показує persisted status.
Під час пошуку перевірити Home/Back/Filters; Save → restart → Saved.
Для безкоштовної перевірки budgeted/mock AI та gateway:

```bash
"$JR_PYTHON" -m pytest -q tests/test_integration_rc.py tests/test_control_runtime_v3.py
```

Команд запуску Mini App backend/frontend, profile editor або синхронізації з ним
ще немає. Не використовуйте вигадані `uvicorn`/`npm` команди. Реальне індивідуальне
AI-оцінювання також не підтримане; платний Full Search — окрема дія з лімітами
та явним UI-підтвердженням згідно RC runbook.

Джерело вимоги раннього ACK:
[Telegram Bot API — CallbackQuery](https://core.telegram.org/bots/api#callbackquery)
та [answerCallbackQuery](https://core.telegram.org/bots/api#answercallbackquery).
