# Austria vacancy sources and reusable components

Research date: 2026-11-04. Priorities below are engineering recommendations for
Vienna junior/student technical roles, not a market-share ranking.
Documentation and accessible pages were inspected; no authenticated search API,
JobSpy execution, paid actor, or connector end-to-end collection was tested.
“Not confirmed” does not mean a private/partner API cannot exist.
Update after this research: bounded live karriere.at HTTP and Playwright probes
now pass. See [implementation checkpoints](PROGRESS.md) for scope and results.
LinkedIn's public search probe now also passes a bounded HTTP/Playwright check;
JobSpy remains an optional future integration rather than a current dependency.

## Source plan

| Source | Evidence and proposed acquisition | Priority / remaining validation |
| --- | --- | --- |
| LinkedIn | Keep existing Gmail alerts. Optional JobSpy discovery adapter; do not treat guest endpoints as an official search API. The [documented Job Posting API](https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview) concerns partner posting integrations. | High coverage priority; direct scraping reliability must be measured. Existing email route stays available. |
| Indeed Austria | Existing email parser/enrichment; JobSpy documents Austria support. | High; trial the adapter on bounded Vienna queries and verify native `jk` identity. |
| karriere.at | [Public Vienna search](https://www.karriere.at/jobs/softwareentwickler/wien) exposes structured listing content and a Job-Alarm option. [robots.txt](https://www.karriere.at/robots.txt) currently has an empty wildcard Disallow and lists sitemaps; that alone does not establish data-use permission. No supported public search API was confirmed. | First new local source. Inspect raw HTTP HTML, native IDs, detail JSON-LD, pagination, current terms and response behavior before enabling. BeautifulSoup is appropriate if HTML parsing is needed. |
| StepStone Austria | Official [API knowledge base](https://api.stepstone.com/) and [Pull Feed](https://api.stepstone.com/knowledge-base/pull/) describe employer feed ingestion. This does not establish a public vacancy-search API. | Coverage priority, later connector. Evaluate alerts/authorized feed or bounded third-party actor trial rather than implementing a fragile large scraper first. |
| Employer ATS boards | [Personio XML](https://developer.personio.de/docs/retrieving-open-job-positions), [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html), [Lever Postings API](https://github.com/lever/postings-api), [Ashby public postings](https://developers.ashbyhq.com/docs/public-job-posting-api). Documented per-company retrieval can include full descriptions. | High after first local source. Requires a curated employer/board watchlist; these endpoints do not discover all Austrian employers. Validate geography and remote eligibility. |
| DEVjobs.at | [Technical job search](https://en.devjobs.at/jobs/explore) is directly relevant to this profile. A supported public retrieval API was not confirmed. | Next specialist source; inspect HTML/structured data, filters and access behavior. |
| jobs.at | [Official site](https://www.jobs.at/) provides Austrian search and email Job-Alarm. Public search API not confirmed. | Next local source; email is a candidate route, but sender/layout fixtures are still required. |
| AMS “alle jobs” | [AMS describes](https://www.ams.at/arbeitsuchende/arbeitslos-was-tun/jobsuche-online-und-mobil/ams-jobsuche-hilfe) aggregation of AMS and external employer listings. Its mentioned HR-API is for employer submissions, not proof of public search/export access. | Broad coverage candidate; confirm supported export/search access and unique relevant yield before building. |
| Adzuna | [Official search API](https://developer.adzuna.com/docs/search), with [app_id/app_key registration](https://developer.adzuna.com/overview). | API-first trial candidate; verify `at` country availability with a real key, Vienna inventory, quotas, attribution terms, redirect behavior, and description completeness. No coverage promise yet. |

Other candidates for later discovery: willhaben Jobs, hokify, university career
boards, and public-sector employer sites. Their integration paths were not audited
in this pass; prioritize measured useful additions over connector count.

## Reuse versus build

**JobSpy — preferred open-source trial for LinkedIn/Indeed.**
[Repository and MIT license](https://github.com/speedyapply/JobSpy) document multiple
boards, global LinkedIn location queries, and Austria for Indeed/Glassdoor. Its
documented board list does not include karriere.at or StepStone. Wrap the Python
package in one optional adapter; normalize NaN/missing values, source errors,
timestamps, URLs, IDs and full-description versus snippet confidence. Verify the
actual installed version's failure reporting; do not turn library failure into
“zero jobs”. Pin after a smoke test. Fork only if an upstream limitation blocks us.

**Apify — candidate for a difficult source.**
For example, the community [Stepstone All-In-One actor](https://apify.com/fatihtahta/stepstone-scraper-fast-reliable-4-1k)
advertises `.at` support and JSON datasets. This is the vendor's claim, not a
verified integration or an official StepStone API. Test a small sample for output
quality, source IDs, Austrian results, failure reporting and total run cost before
subscribing. Keep the provider replaceable. Never submit candidate profile/email
content when a keyword/location query suffices.

**BeautifulSoup — parsing component.**
Useful for board-specific DOM extraction after HTTP retrieval. It does not provide
discovery strategy, retries, pagination, scheduling, JavaScript execution,
checkpointing, deduplication or source availability. Existing deterministic parsers
can stay; add BeautifulSoup where fixtures demonstrate simpler, more robust code.

**Playwright — included in V1 direct collection.**
Use one shared Chromium transport for board navigation/rendering and source-specific
extraction adapters. Keep direct discovery independent of email. Use API/HTTP where
they deliver the same data more simply; BeautifulSoup remains optional. Pin browser
and package versions together as described in the [official browser documentation](https://playwright.dev/python/docs/browsers).
Browser timeouts, login expiry and blocked pages must be reported, not interpreted
as zero jobs. See [V1 operation](MVP_V1.md) for isolation and deployment requirements.
Scrapy is not needed for the initial single-process watchlist.

## Trial and rollout criteria

Use the same saved German/English queries across sources: junior/backend/Python,
Werkstudent/working student, Praktikum/internship, automation/integrations and data
engineering; Vienna/Wien, hybrid Vienna, remote Austria. Do not require “junior”
in every query; include adjacent titles and preserve uncertain roles for review.

Start with a bounded 20–50 listing trial per connector. Compare to existing alerts
and manually inspect native IDs, URL destination, location, published time,
description completeness, duplicates and expired listings. Then evaluate over a
proposed seven-day observation period:

- Newly discovered relevant vacancies not already present.
- Full-description yield, delay from publication, invalid-record rate.
- Successful/partial/blocked runs, requests and elapsed time.
- Total provider cost per new relevant vacancy and maintenance effort.

Enable sources individually after fixture tests and a live smoke check. Preserve
all source records; never infer expiry from an incomplete trial or an empty alert.
