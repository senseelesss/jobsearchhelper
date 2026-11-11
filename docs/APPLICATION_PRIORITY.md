# Filtering accuracy and application priority — 2026-11-05

Local checkpoint after b9a7eb3. No paid requests, real Telegram sends, applications,
remote deployment or GitHub push were made. Validation used isolated copies of
the existing 34-job database/workbook snapshot and anonymized fixtures.

## What changed

`eligibility.py` derives application eligibility and priority independently of the
stored AI technical match. No job-score or application-state migration is needed.
The private candidate profile/CV was not edited, so its existing score cache hash
remains valid. Verified skills continue to feed the existing LLM matcher.
Search preferences live in packaged `eligibility_rules.v1.json`; optional private
`search_preferences.target_hours/acceptable_hours` override the hour bounds.
No citizenship, permit or EU work authorization is inferred from Vienna residence.

Derived fields in JSON/Excel: technical_match_score, hours_fit, seniority_fit and
level, required/preferred years, location_fit, role_fit, application_priority,
application_eligibility, eligibility_reasons/evidence, exclusion_reason,
evidence_quality, and policy version. The original match_score remains available.
Daily report includes the five highest-ranked **actionable A/B** jobs in
`top_applications`. No score is invented for Tier B.

### Why the previous filter was inaccurate

- Hours used the first numeric weekly phrase and a broad part-time keyword.
  Explicit 38.5/40h roles could stay unknown, while salary reference hours or
  generic company benefits could be mistaken for this vacancy's hours.
- Broad IT/software/integration terms admitted managers, architects and unrelated
  domains. A technology keyword in a management job did not prove an actionable
  development role. Conversely, Marketing Technology in a student IT-support
  title is not the same as a nontechnical marketing role.
- The experience regex started at five years, searched combined snippet/JD text
  without required/preferred distinction and could retain stale alert requirements.
- A technical score and screening label did not independently encode employment,
  seniority or location constraints. Sorting only by AI/hours hid practical reasons.
- The eight-item outbox preserved overflow, but without an aging slot a continuous
  stream of new recommendations could indefinitely delay older pending items.

## Deterministic policy

Hard, explicit mismatches yield application_eligibility=ineligible and EXCLUDED,
regardless of technical match. This includes incompatible hours, senior/lead/staff/
architect/manager requirements, required professional experience >=4 years,
clearly unrelated roles, incompatible work location, expiry and prior application
state. Missing information yields review_required, not an invented confirmation.

Hours classify into exactly one category:

- confirmed_target: an explicit advertised range supports 20–25h/week.
- acceptable: compatible with 15–30h/week, outside the target range.
- flexible_part_time: explicit flexibility without confirmed target hours.
- unknown: insufficient or contradictory evidence; retain supporting phrases.
- confirmed_mismatch: explicit full-time-only or a range outside 15–30h.

Recognized evidence includes German/English weekly units, role-title hour tags,
structured hours_min/max and employment_type/work_hours inputs. JobPosting JSON-LD
employmentType/workHours are preserved as labelled description evidence through
the existing enrichment adapters and SQLite description field. Full-time salary
basis and generic employer statements do not establish the vacancy's hours.
Werkstudent alone is positive seniority evidence, not proof of weekly hours.
Explicit full-time/part-time alternatives remain compatible. Contradictions remain
uncertain and LOW review, with the evidence retained. Target-compatible does not
mean a contract has been independently verified.

Seniority uses title and scoped job requirements. Required 0–2 years is compatible,
3 years is possible/LOW, >=4 is incompatible; preferred experience is not required.
A senior colleague/company-history paragraph does not turn a junior job senior.
A current full JD supersedes an older snippet during screening. Rules version
`screening-v4-eligibility` triggers ordinary re-screening on the next authorized
run, preserving source IDs and historical application/notification state.

Role relevance is based on the stated technical search interests, not a claim
that the candidate has every mentioned skill. Generic Software Engineer without
stack evidence is not a strong technical match. Unrelated primary stacks/domains
are LOW review; sales/primarily managerial/nontechnical roles are excluded.
Application Manager remains distinguishable from IT Domain/Engineering Manager.

Vienna/hybrid Vienna and explicitly Austria-remote are preferred. EU/unspecified
remote remains unverified until work-from-Austria eligibility is established.
Explicit foreign-only work locations are incompatible. A citizenship requirement
without verified candidate authorization facts stays unverified, not a guessed
legal conclusion. Verified EU-remote eligibility can be supplied privately under
work_authorization; never fabricate it.

Priority points are transparent in the packaged policy: hours, seniority, role,
location, current JD quality, posting freshness and up to ten points from the
existing technical score. Default HIGH/MEDIUM thresholds are 75/45. Unknown hours,
unspecified seniority or unverified location cap confidence/priority. Three-year
requirements, contradictory hours, unrelated primary stack, AI skip or technical
match below 50 cap priority at LOW; these soft signals do not themselves prove
employment ineligibility. The points are not another 0–100 technical score.

HIGH examples are tested: 20h working-student software development; 25h junior
Python; junior automation with explicit part-time flexibility. Unknown-hours
junior backend/JavaScript and unspecified software positions remain MEDIUM review.

### Configuration

Copy the packaged eligibility_rules.v1.json to a private configuration path and
set JOB_RADAR_ELIGIBILITY_RULES to its absolute path. Hours bounds, experience
thresholds, technology/domain lists, point weights, thresholds and notification
aging are configurable. Reports include a content-based policy ID. Defaults match
the user's stated 20–25 target and 15–30 acceptable range. The template candidate
profile includes optional search preferences but no invented experience or permits.
Policy changes do not invalidate stored technical scores or resend vacancies.

## Scoring and Telegram

The scoring queue rejects explicit deterministic exclusions before spending a
call. It prefers confirmed target hours, working-student/junior roles, relevant
stack, full JD, new/recent input and unscored backlog. Existing cache/cooldown and
FIFO backlog reservation remains: one older slot for budgets >=2, or every
third run at budget 1. Old items no longer take the whole budget ahead of new
target-hours student roles. Eligible adjacent student-support roles can enter
scoring even when broad screening labelled them review; architects/full-time-only
roles cannot consume their budget. No new LLM call type was introduced.

Tier A retains the existing current-score recommendation filter; Tier B remains
UNSCORED. Both show deterministic priority, hours, seniority and a short reason.
Failed scores and hard rejections are not promoted. Legacy scored-card selection
also checks hard eligibility, but daily remains the production workflow.

New HIGH Tier B jobs sort before LOW Tier A jobs. The final fitting slot is reserved
for an oldest pending item after three days when necessary. Formatting remains
bounded to eight items/3,800 UTF-16 units; long URLs use an Open button. Only selected
items are acknowledged/reserved. Overflow remains pending; an unknown delivery
is held for operator reconciliation. Same-day immutable payload and cross-day
native/semantic deduplication are preserved. Score/priority changes alone do not
trigger another alert. Top-five ranking is separate from the final backlog slot.

## Observed comparison on the existing dataset

Sources: outputs/production-launch-smoke/vacancies.xlsx and its corresponding
jobs.db, 34 unique saved source/semantic vacancies. The smaller outputs/daily
workbook had ten rows; its state was not merged or overwritten. The old code was
loaded from b9a7eb3; the new code ran against a separate consistent copy. All
validation socket connections were blocked, and Telegram was an in-process mock.

| Measure | Before | After |
| --- | ---: | ---: |
| Unique vacancies retained | 34 | 34 |
| Broad rule-actionable/review pool | 22 | 15 |
| Actionable A/B queue | 20 | 13 |
| Eligible for scoring | 19 | 12 |
| Mock delivered items | 8 | 8 |
| Deferred notification backlog for synthetic chat | 12 | 5 |
| Stored technical score rows changed | 0 | 0 |

Current priority partition: **HIGH 0 / MEDIUM 9 / LOW 6 / EXCLUDED 19**, total 34.
Hours partition: target 1, acceptable 0, flexible 0, unknown 30, mismatch 3, total
34. There are 11 seniority mismatches and 3 explicit full-time mismatches; reasons
can overlap and must not be added to the priority totals. Actionable AI-recommended
A=0, unscored B=13. The two existing current scores are review/45 and review/42,
not skip; the earlier staging notes were corrected. They are LOW/manual review
records, and stay outside the unchanged Tier A threshold.

Concrete corrections from saved evidence:

- Kommunalkredit IT Domain Manager, AWS Solutions Architect and Kapsch Business
  Intelligence Architect: formerly eligible/unknown-hours; now excluded for role
  seniority, without assuming facts from company names.
- jonnect Test Automation with Python: formerly unknown hours; saved JD explicitly
  says Vollzeit and 38.5 Stunden, now excluded for hours.
- Raiffeisen student support/Marketing Technology, 25h/week: retained as MEDIUM
  adjacent IT review, not discarded because the title contains Marketing.
- BRZ senior Python/Java: excluded for seniority, but its collective-agreement
  full-time salary reference does not falsely prove this vacancy's hours.
- 5G Wireless Integration Engineer: LOW domain/stack review, not a strong backend
  fit just because Integration appears in its title.

The snapshot contains no sufficiently evidenced HIGH application candidate.
Representative anonymized fixtures cover the supplied Siemens/Helvetia/BRZ-like
examples; they are not claims about live company vacancies absent from the data.

### First five to investigate from this snapshot

All five are MEDIUM and unscored. Verify role requirements and current availability
before applying; only the first has confirmed target-hours evidence.

1. Raiffeisen — Student Support, Agile Delivery / Marketing Technology (25h/week).
2. Deloitte — (Junior) Consultant, Solution Developer (hours unknown).
3. Wolf Theiss — Automation & Integration Engineer (hours unknown).
4. EY — AI Agents & Business Automation (hours unknown).
5. XXXLdigital — Product Content Engineer, AI & Text Automation (hours unknown).

The generated after-copy workbook has the same ranking first, priority colors,
filterable fit/eligibility/evidence/reason columns, original source/application IDs
and unchanged status-edit sheet semantics. Values are literal strings, not
website-supplied formulas. Export was reopened, inspected and visually rendered.
It is a snapshot: edit application states through the existing validated import;
priority changes are made through evidence/rules and regenerated by the script.

## Validation and remaining limits

251 tests pass, including anonymized examples, German/English hours, conflicting
metadata, salary basis, preferred/required experience, unrelated senior text,
remote-country uncertainty, priority/AI ordering, backlog fairness, A/B filters,
status persistence, duplicate prevention and existing SQLite migration tests.
Ruff and whitespace checks pass.
The publishable-copy suite also passes with the blank profile template. A wheel
build verified both policy JSON files are included and private data directories
are excluded; target Ubuntu execution is still a separate acceptance check.
Private validation artifacts are under
outputs/eligibility-validation and are ignored; do not deploy copies containing
mock deliveries. Original DB tables and profile were not modified. No real paid
usage or Telegram delivery occurred.

Four of the 34 saved vacancies have full JDs; most hours remain unknown. Regex
rules cannot guarantee understanding every layout, idiom, negation or employment
contract. Requirement/source conflicts stay visible rather than being hidden by
an invented score. Work authorization requires verified candidate facts. No
current website availability, actual human application or Ubuntu scheduled run
was verified here. The local classifier is validated; unattended production
acceptance remains pending on the approved Ubuntu host, with real-source,
callback, reboot, backup and monitoring checks from PRODUCTION_RUNBOOK.md.
