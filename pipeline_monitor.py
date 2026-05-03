#!/usr/bin/env python3
"""
gitlab-pipeline-monitor
-----------------------
Pulls GitLab CI pipeline metrics for a project and reports on
failure rates, slow stages, and flaky jobs. Useful for spotting
pipeline reliability problems before they become team blockers.

Usage:
    python3 pipeline_monitor.py --project-id 123
    python3 pipeline_monitor.py --project-id 123 --days 7
    python3 pipeline_monitor.py --project-id 123 --branch main --slack-webhook https://...
    python3 pipeline_monitor.py --project-id 123 --output json

Requirements:
    pip install requests
    Set GITLAB_TOKEN env var with a personal access token (read_api scope)
    Set GITLAB_URL env var (default: https://gitlab.com)
"""

import argparse
import json
import os
import sys
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

GITLAB_URL = os.environ.get("GITLAB_URL", "https://gitlab.com")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")

# Thresholds
FAILURE_RATE_THRESHOLD = 20    # % pipeline failure rate to flag
SLOW_STAGE_THRESHOLD_MINS = 15 # flag stages slower than this
FLAKY_JOB_THRESHOLD = 3        # flag jobs that failed+passed more than this many times


def get_headers():
    if not GITLAB_TOKEN:
        print("ERROR: GITLAB_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)
    return {"PRIVATE-TOKEN": GITLAB_TOKEN}


def get_pipelines(project_id, branch=None, days=7):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    params = {
        "updated_after": since,
        "per_page": 100,
        "order_by": "updated_at",
        "sort": "desc"
    }
    if branch:
        params["ref"] = branch

    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/pipelines"
    pipelines = []
    page = 1

    while True:
        params["page"] = page
        resp = requests.get(url, headers=get_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        pipelines.extend(data)
        page += 1
        if len(data) < 100:
            break

    return pipelines


def get_pipeline_jobs(project_id, pipeline_id):
    url = f"{GITLAB_URL}/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs"
    resp = requests.get(url, headers=get_headers(), params={"per_page": 100}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def calculate_duration_mins(job):
    if job.get("duration"):
        return round(job["duration"] / 60, 2)
    return 0


def analyze_pipelines(pipelines, project_id):
    total = len(pipelines)
    if total == 0:
        return None

    status_counts = defaultdict(int)
    stage_durations = defaultdict(list)
    job_results = defaultdict(lambda: {"passed": 0, "failed": 0})
    slow_pipelines = []

    for pipeline in pipelines:
        status_counts[pipeline["status"]] += 1

        # Sample jobs from failed and slow pipelines
        if pipeline["status"] in ["failed", "success"] and pipeline.get("duration"):
            duration_mins = pipeline["duration"] / 60
            if duration_mins > SLOW_STAGE_THRESHOLD_MINS:
                slow_pipelines.append({
                    "id": pipeline["id"],
                    "ref": pipeline["ref"],
                    "duration_mins": round(duration_mins, 1),
                    "status": pipeline["status"],
                    "created_at": pipeline["created_at"]
                })

        # Get jobs for failed pipelines to find flaky/broken jobs
        if pipeline["status"] == "failed":
            try:
                jobs = get_pipeline_jobs(project_id, pipeline["id"])
                for job in jobs:
                    stage = job.get("stage", "unknown")
                    if job.get("duration"):
                        stage_durations[stage].append(job["duration"] / 60)
                    job_results[job["name"]][job["status"]] = \
                        job_results[job["name"]].get(job["status"], 0) + 1
            except Exception:
                pass

    failed = status_counts.get("failed", 0)
    success = status_counts.get("success", 0)
    failure_rate = round((failed / total) * 100, 1) if total > 0 else 0

    # Find slow stages
    slow_stages = []
    for stage, durations in stage_durations.items():
        avg = sum(durations) / len(durations)
        if avg > SLOW_STAGE_THRESHOLD_MINS:
            slow_stages.append({
                "stage": stage,
                "avg_duration_mins": round(avg, 1),
                "sample_count": len(durations)
            })
    slow_stages.sort(key=lambda x: x["avg_duration_mins"], reverse=True)

    # Find flaky jobs (failed multiple times but also passed)
    flaky_jobs = []
    for job_name, results in job_results.items():
        if results.get("failed", 0) >= FLAKY_JOB_THRESHOLD and results.get("success", 0) > 0:
            flaky_jobs.append({
                "job": job_name,
                "failed": results["failed"],
                "passed": results.get("success", 0)
            })
    flaky_jobs.sort(key=lambda x: x["failed"], reverse=True)

    return {
        "total_pipelines": total,
        "status_breakdown": dict(status_counts),
        "failure_rate_pct": failure_rate,
        "failed_count": failed,
        "success_count": success,
        "slow_pipelines": slow_pipelines[:5],
        "slow_stages": slow_stages[:5],
        "flaky_jobs": flaky_jobs[:10],
        "alerts": []
    }


def build_alerts(analysis):
    alerts = []
    if analysis["failure_rate_pct"] > FAILURE_RATE_THRESHOLD:
        alerts.append(f"HIGH FAILURE RATE: {analysis['failure_rate_pct']}% "
                      f"({analysis['failed_count']}/{analysis['total_pipelines']} pipelines)")
    if analysis["slow_stages"]:
        top = analysis["slow_stages"][0]
        alerts.append(f"SLOW STAGE: '{top['stage']}' averaging "
                      f"{top['avg_duration_mins']} mins")
    if analysis["flaky_jobs"]:
        top = analysis["flaky_jobs"][0]
        alerts.append(f"FLAKY JOB: '{top['job']}' failed {top['failed']} times "
                      f"but also passed {top['passed']} times")
    return alerts


def send_slack(webhook_url, analysis, project_id, days):
    alerts = analysis["alerts"]
    if not alerts and analysis["failure_rate_pct"] <= FAILURE_RATE_THRESHOLD:
        return

    lines = [
        f"*GitLab Pipeline Report* — Project {project_id} (last {days} days)",
        f"Pipelines: {analysis['total_pipelines']} | "
        f"Failure rate: {analysis['failure_rate_pct']}%",
        ""
    ]
    for alert in alerts:
        lines.append(f"⚠️ {alert}")

    requests.post(webhook_url, json={"text": "\n".join(lines)}, timeout=5)


def print_report(analysis, project_id, days, branch):
    print("\n" + "=" * 60)
    print(f"  GITLAB PIPELINE REPORT — Project {project_id}")
    print(f"  Branch: {branch or 'all'} | Last {days} days")
    print("=" * 60)

    print(f"\n  Total pipelines:  {analysis['total_pipelines']}")
    print(f"  Failure rate:     {analysis['failure_rate_pct']}%  "
          f"({analysis['failed_count']} failed / {analysis['success_count']} passed)")
    print(f"  Status breakdown: {analysis['status_breakdown']}")

    if analysis["alerts"]:
        print("\n  ⚠️  Alerts:")
        for alert in analysis["alerts"]:
            print(f"     • {alert}")

    if analysis["slow_stages"]:
        print("\n  🐢 Slow stages (avg duration):")
        for s in analysis["slow_stages"]:
            print(f"     • {s['stage']}: {s['avg_duration_mins']} mins")

    if analysis["flaky_jobs"]:
        print("\n  🎲 Flaky jobs:")
        for j in analysis["flaky_jobs"]:
            print(f"     • {j['job']}: {j['failed']} fails / {j['passed']} passes")

    if analysis["slow_pipelines"]:
        print("\n  🐌 Slowest pipelines:")
        for p in analysis["slow_pipelines"]:
            print(f"     • #{p['id']} [{p['ref']}] {p['duration_mins']} mins — {p['status']}")

    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="GitLab CI pipeline monitor")
    parser.add_argument("--project-id", required=True, help="GitLab project ID")
    parser.add_argument("--branch", help="Filter by branch (default: all branches)")
    parser.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    parser.add_argument("--slack-webhook", help="Slack webhook URL for alerts")
    args = parser.parse_args()

    print(f"Fetching pipelines for project {args.project_id}...")
    pipelines = get_pipelines(args.project_id, args.branch, args.days)

    if not pipelines:
        print("No pipelines found for the given parameters.")
        sys.exit(0)

    analysis = analyze_pipelines(pipelines, args.project_id)
    analysis["alerts"] = build_alerts(analysis)

    if args.output == "json":
        print(json.dumps(analysis, indent=2))
    else:
        print_report(analysis, args.project_id, args.days, args.branch)

    if args.slack_webhook:
        send_slack(args.slack_webhook, analysis, args.project_id, args.days)

    sys.exit(1 if analysis["alerts"] else 0)


if __name__ == "__main__":
    main()
