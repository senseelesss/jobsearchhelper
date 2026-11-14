# v3.1 — початковий аудит, не завершена реалізація

Baseline: f6f314d, feat/telegram-experience-v3. 434 tests passed.

Перевірений шлях: CLI daily → daily_cycle → ingestion/deduplication →
repository.screen_keys → review_rows → bounded _enrich_review → rescreen →
scoring_plan → score_jobs → OpenAIProvider.score → JobScore validation →
SQLite score cache → review_rows → ranking/outbox/Telegram.

scoring_plan викликає assess_application до provider і пропускає ineligible.
Отже, AI не може перевірити неоднозначний випадок, який deterministic policy
вже відсікла. Це точка для подальшого аналізу хибних виключень, а не доказ,
що всі наявні виключення неправильні. Cache identity включає semantic key,
profile hash, description hash, prompt version і model.

JobScore зараз містить загальний score/confidence/recommendation, strengths,
missing requirements, risks і summary. Окремих validated fit dimensions та
цитованих evidence для освіти/годин/мови немає. Поточний prompt свідомо відділяє
working hours від technical score. Новий evaluator потребує узгодженої зміни
schema, prompt version, policy, persistence/readers та mocked regressions.

Активні шари: sources/parsers; ingestion/storage; screening/work_hours/eligibility;
candidate/matching/ai; daily/runner; control/telegram; exporting/workbook; CLI.
Legacy CLI і runner не слід видаляти: це compatibility entry points із тестами.
Історичні документи не є доведено disposable. Повний import/dead-code audit,
інвентар ignored artifacts і поточний GitHub PR audit ще не завершені.

Cleanup: прибрано лише зайвий .venv/ із .gitignore — .venv*/ вже покриває його.
Файлів не видалено й не переміщено. Серед tracked *.db, *.sqlite3, .env та
candidate_profile* знайдено лише публічний candidate_profile.example.yaml.
Це вузька перевірка шляхів, не повний security scan. Приватні дані не відкривалися.

Залишаються фази 3–7: AI-first evaluator, verified hard constraints, enrichment
backlog refinement, integration, labeled evaluation та deployment documentation.
Жодного покращення accuracy або готовності v3.1 не заявлено. Міграцій,
нових dependencies, paid calls, Telegram sends чи deployment немає.
