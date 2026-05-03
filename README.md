# gitlab-pipeline-monitor

A Python script that pulls GitLab CI pipeline metrics and reports on failure rates, slow stages, and flaky jobs. Helps SRE and DevOps teams spot pipeline reliability problems before they become team blockers.

## What it reports

- **Failure rate** — % of pipelines that failed over a configurable time window
- **Slow stages** — CI stages averaging above a configurable threshold
- **Flaky jobs** — jobs that repeatedly fail but also pass (intermittent failures)
- **Slow pipelines** — individual pipelines taking too long
- **Slack alerts** — optional webhook notification when thresholds are exceeded

## Setup

```bash
pip install requests

export GITLAB_TOKEN=your_personal_access_token   # needs read_api scope
export GITLAB_URL=https://gitlab.com             # or your self-hosted URL
```

## Usage

```bash
# Basic report for a project (last 7 days, all branches)
python3 pipeline_monitor.py --project-id 123

# Filter to main branch, last 14 days
python3 pipeline_monitor.py --project-id 123 --branch main --days 14

# Output as JSON
python3 pipeline_monitor.py --project-id 123 --output json

# Send Slack alert if thresholds exceeded
python3 pipeline_monitor.py --project-id 123 --slack-webhook https://hooks.slack.com/services/xxx
```

## Finding your project ID

In GitLab, go to your project → Settings → General. The project ID is at the top.

## Example output

```
============================================================
  GITLAB PIPELINE REPORT — Project 123
  Branch: main | Last 7 days
============================================================

  Total pipelines:  47
  Failure rate:     27.7%  (13 failed / 34 passed)
  Status breakdown: {'success': 34, 'failed': 13}

  ⚠️  Alerts:
     • HIGH FAILURE RATE: 27.7% (13/47 pipelines)
     • SLOW STAGE: 'integration-tests' averaging 22.4 mins
     • FLAKY JOB: 'e2e-chrome' failed 5 times but also passed 8 times

  🐢 Slow stages (avg duration):
     • integration-tests: 22.4 mins
     • build-docker: 18.1 mins

  🎲 Flaky jobs:
     • e2e-chrome: 5 fails / 8 passes
     • db-migration-check: 3 fails / 12 passes
============================================================
```

## Running as a scheduled GitLab CI job

```yaml
pipeline-health-check:
  stage: monitoring
  script:
    - pip install requests
    - python3 pipeline_monitor.py --project-id $CI_PROJECT_ID --branch main
  rules:
    - if: '$CI_PIPELINE_SOURCE == "schedule"'
  only:
    - schedules
```

## Configuration

Edit thresholds at the top of the script:

```python
FAILURE_RATE_THRESHOLD = 20     # % failure rate to trigger alert
SLOW_STAGE_THRESHOLD_MINS = 15  # flag stages slower than this
FLAKY_JOB_THRESHOLD = 3         # flag jobs failing this many times
```

## Exit codes

- `0` — no alerts triggered
- `1` — one or more thresholds exceeded
