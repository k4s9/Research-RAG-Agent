"""Freeze reviewed task manifests, run paired baselines and report every attempt.

No live calls are made by freeze or summarize. run requires a frozen manifest with
explicit model, repetition and aggregate call/token budgets. Human ratings are
separate inputs; missing ratings never become successful task scores.
"""

import argparse
import hashlib
import json
import math
import subprocess
import time
import uuid
from pathlib import Path

import requests


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tasks_at(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def freeze(config_path, destination):
    config = load_json(config_path)
    required = {
        "dataset",
        "rubric",
        "corpus",
        "model",
        "repetitions",
        "strategies",
        "run_budget",
        "max_model_calls",
        "max_total_tokens",
        "conditions",
        "reviewed_by",
        "split",
        "expected_tasks",
    }
    if required - config.keys():
        raise ValueError(f"Missing freeze fields: {sorted(required - config.keys())}")
    if not config["reviewed_by"] or not config["model"] or type(config["repetitions"]) is not int or config["repetitions"] < 1:
        raise ValueError("Review, model and repetitions must be explicit")
    if not config["strategies"] or len(set(config["strategies"])) != len(config["strategies"]) or set(config["strategies"]) - {"b0", "b1", "b2"}:
        raise ValueError("Use unique explicit b0/b1/b2 strategies")
    for key in ("max_model_calls", "max_total_tokens"):
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    from src.core.agent.runtime import RunBudget

    config["run_budget"] = RunBudget.model_validate(config["run_budget"]).model_dump()
    if not config["conditions"].get("runtime"):
        raise ValueError("Freeze explicit runtime identity conditions")
    tasks = tasks_at(config["dataset"])
    if len(tasks) != config["expected_tasks"] or len({t["task_id"] for t in tasks}) != len(tasks):
        raise ValueError("Task count/IDs do not match reviewed configuration")
    for task in tasks:
        required_task = {
            "task_id",
            "family",
            "project_ids",
            "turns",
            "required_fields",
            "key_facts",
            "acceptable_evidence",
            "boundary_behavior",
        }
        if required_task - task.keys() or not task["turns"]:
            raise ValueError(f"Incomplete task {task.get('task_id')}")
    files = [config["dataset"], config["rubric"], *config["corpus"]]
    if not config["corpus"]:
        raise ValueError("A corpus snapshot is required")
    config["hashes"] = {str(p): sha(Path(p)) for p in files}
    config["code_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    config["code_hashes"] = {str(p): sha(p) for p in sorted(Path("src").rglob("*.py"))}
    config["evaluator_hash"] = sha(Path(__file__))
    config["code_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True))
    config["frozen_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    destination = Path(destination)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
    print(f"Frozen {len(tasks)} {config['split']} tasks: {destination}")


def verify_frozen(config):
    for path, digest in {**config.get("hashes", {}), **config.get("code_hashes", {})}.items():
        if sha(Path(path)) != digest:
            raise ValueError(f"Frozen file changed: {path}; create a new experiment version")
    if not config.get("frozen_at") or not config.get("hashes") or not config.get("code_hashes"):
        raise ValueError("Configuration is not frozen")
    if config.get("evaluator_hash") != sha(Path(__file__)):
        raise ValueError("Evaluator changed; create a new experiment version")


def verify_runtime(config, actual):
    if actual["model"] != config["model"] or actual["code_hashes"] != config["code_hashes"]:
        raise ValueError("Running server model/code differs from frozen manifest")
    for key, expected in config["conditions"]["runtime"].items():
        if actual.get(key) != expected:
            raise ValueError(f"Running server {key} differs from frozen manifest")


def run(manifest, output, api_url):
    config = load_json(manifest)
    verify_frozen(config)
    # Check the running API's identity, not just the evaluator checkout.
    identity = requests.get(api_url + "/api/v1/chat/runtime", timeout=10)
    identity.raise_for_status()
    actual = identity.json()
    verify_runtime(config, actual)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "manifest.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))
    (output / "runtime.json").write_text(json.dumps(actual, ensure_ascii=False, indent=2))
    used_calls = used_tokens = 0
    with (output / "samples.jsonl").open("w", encoding="utf-8") as stream:
        for task in tasks_at(config["dataset"]):
            for repeat in range(config["repetitions"]):
                for strategy in config["strategies"]:
                    session = str(uuid.uuid4())
                    record = dict(
                        task_id=task["task_id"],
                        family=task["family"],
                        repeat=repeat,
                        system=strategy,
                        turns=[],
                        reservations=[],
                        success=None,
                        error=None,
                    )
                    start = time.monotonic()
                    run_id = None
                    try:
                        for turn in task["turns"]:
                            reserve_calls = config["run_budget"]["model_calls"]
                            reserve_tokens = config["run_budget"]["total_tokens"]
                            if (
                                used_calls + reserve_calls > config["max_model_calls"]
                                or used_tokens + reserve_tokens > config["max_total_tokens"]
                            ):
                                raise RuntimeError("aggregate experiment budget exhausted")
                            # Reservation includes failures with unknown usage.
                            used_calls += reserve_calls
                            used_tokens += reserve_tokens
                            reservation = dict(model_calls=reserve_calls, tokens=reserve_tokens, usage_unknown=True)
                            record["reservations"].append(reservation)
                            created = requests.post(
                                api_url + "/api/v1/chat/runs",
                                json=dict(
                                    session_id=session,
                                    project_ids=task["project_ids"],
                                    message=turn["message"],
                                    task_type=turn.get("task_type", task.get("task_type", "qa")),
                                    strategy=strategy,
                                    budget=config["run_budget"],
                                ),
                                timeout=15,
                            )
                            created.raise_for_status()
                            run_id = created.json()["run_id"]
                            reservation["run_id"] = run_id
                            deadline = (
                                time.monotonic() + config["run_budget"]["active_seconds"] + 30
                            )
                            while True:
                                response = requests.get(
                                    api_url + f"/api/v1/chat/runs/{run_id}", timeout=10
                                )
                                response.raise_for_status()
                                data = response.json()
                                if data["status"] != "running":
                                    break
                                if time.monotonic() > deadline:
                                    requests.post(
                                        api_url + f"/api/v1/chat/runs/{run_id}/cancel", timeout=10
                                    ).raise_for_status()
                                    raise TimeoutError("client deadline")
                                time.sleep(0.2)
                            execution = (data.get("state") or {}).get("execution") or {}
                            usage = execution.get("usage") or {}
                            if not usage:
                                raise RuntimeError("terminal run has no usage checkpoint")
                            used_calls += usage.get("model_calls", reserve_calls) - reserve_calls
                            used_tokens += (
                                usage.get("reserved_tokens", reserve_tokens) - reserve_tokens
                            )
                            reservation.update(
                                model_calls=usage.get("model_calls", reserve_calls),
                                tokens=usage.get("reserved_tokens", reserve_tokens),
                                usage_unknown=bool(usage.get("unknown_usage_calls", 0)),
                            )
                            record["turns"].append(data)
                            if data["status"] not in ("completed", "insufficient_evidence"):
                                raise RuntimeError(data["status"] + ": " + str(data.get("error")))
                            if turn.get("task_type", task.get("task_type", "qa")) != "qa" and not (data.get("state") or {}).get("report_id"):
                                raise RuntimeError("required report artifact missing: " + str(execution.get("reason")))
                            run_id = None
                    except Exception as exc:
                        record["error"] = str(exc)
                        if run_id:
                            # Also stop a waiting-user or orphaned run. Keep the last state for diagnosis.
                            try:
                                requests.post(api_url + f"/api/v1/chat/runs/{run_id}/cancel", timeout=10).raise_for_status()
                                last = requests.get(api_url + f"/api/v1/chat/runs/{run_id}", timeout=10)
                                last.raise_for_status()
                                if not record["turns"] or record["turns"][-1].get("run_id") != run_id:
                                    record["turns"].append(last.json())
                            except Exception as cleanup_error:
                                record["cleanup_error"] = type(cleanup_error).__name__
                    record["attempted"] = bool(record["reservations"])
                    record["latency_seconds"] = time.monotonic() - start if record["attempted"] else None
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
    summarize(output / "samples.jsonl", output)


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def summarize(samples_path, output):
    samples = tasks_at(samples_path)
    summary = {}
    for system in sorted({s["system"] for s in samples}):
        rows = [s for s in samples if s["system"] == system]
        measured_input = measured_output = reserved = unknown = calls = 0
        costs = []
        tool_calls = 0
        active_seconds = 0.0
        for row in rows:
            for turn in row.get("turns", []):
                usage = ((turn.get("state") or {}).get("execution") or {}).get("usage") or {}
                measured_input += usage.get("prompt_tokens", 0)
                measured_output += usage.get("completion_tokens", 0)
                reserved += usage.get("reserved_tokens", 0)
                unknown += usage.get("unknown_usage_calls", 0)
                calls += usage.get("model_calls", 0)
                tool_calls += usage.get("tool_calls", 0)
                active_seconds += usage.get("active_seconds", 0)
                costs.append(usage.get("cost"))
        latencies = [r["latency_seconds"] for r in rows if r.get("latency_seconds") is not None]
        unobserved = [
            reservation for row in rows for reservation in row.get("reservations", [])
            if reservation.get("run_id") not in {
                t.get("run_id") for t in row.get("turns", [])
                if ((t.get("state") or {}).get("execution") or {}).get("usage")
            }
        ]
        summary[system] = dict(
            n=len(rows),
            attempted=sum(r.get("attempted", True) for r in rows),
            failures=sum(bool(r["error"]) for r in rows),
            judged=sum(r.get("success") is not None for r in rows),
            successes=sum(r.get("success") is True and not r["error"] for r in rows),
            success_rate=(
                sum(r.get("success") is True and not r["error"] for r in rows) / len(rows)
                if all(r.get("success") is not None or r["error"] for r in rows)
                else None
            ),
            p50_seconds=percentile(latencies, 0.5),
            p95_seconds=percentile(latencies, 0.95),
            measured_input_tokens=measured_input,
            measured_output_tokens=measured_output,
            conservative_accounted_tokens=reserved,
            unknown_usage_calls=unknown,
            model_calls=calls,
            tool_calls=tool_calls,
            active_seconds=active_seconds,
            unobserved_reserved_model_calls=sum(r["model_calls"] for r in unobserved),
            unobserved_reserved_tokens=sum(r["tokens"] for r in unobserved),
            cost=sum(costs) if costs and not unobserved and all(c is not None for c in costs) else None,
        )
    output = Path(output)
    (output / "metrics.json").write_text(json.dumps(summary, indent=2))
    lines = [
        "# Task evaluation",
        "",
        "Conditions, corpus/code hashes, model, budgets and baseline settings: manifest.json.",
        "All attempts including failures: samples.jsonl. Missing manual ratings are null, not successful tasks.",
        "",
        "| System | N | Failures | Judged | Success | P50 s | P95 s | Input/output tokens | Unknown calls | Cost |",
        "|---|---:|---:|---:|---|---:|---:|---|---:|---|",
    ]
    for name, m in summary.items():
        lines.append(
            f"| {name} | {m['n']} | {m['failures']} | {m['judged']} | {m['success_rate']} | {m['p50_seconds']} | {m['p95_seconds']} | {m['measured_input_tokens']}/{m['measured_output_tokens']} | {m['unknown_usage_calls']} | {m['cost'] if m['cost'] is not None else 'unknown'} |"
        )
    lines += ["", "## Failures", ""]
    lines += [
        f"- {r['task_id']} / {r['system']} / repeat {r['repeat']}: {r['error']}"
        for r in samples
        if r["error"]
    ]
    lines += [
        "",
        "P50/P95 use nearest rank including attempted failures; budget-blocked unattempted rows remain unsuccessful in the task denominator but have no latency. Small smoke sets do not support population latency or success claims.",
        "Repeated runs are not independent tasks. Report paired task/category results and bootstrap intervals for the held-out experiment.",
    ]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("config")
    f.add_argument("destination")
    r = sub.add_parser("run")
    r.add_argument("manifest")
    r.add_argument("output")
    r.add_argument("--api", default="http://127.0.0.1:8002")
    s = sub.add_parser("summarize")
    s.add_argument("samples")
    s.add_argument("output")
    args = parser.parse_args()
    if args.action == "freeze":
        freeze(args.config, args.destination)
    elif args.action == "run":
        run(args.manifest, args.output, args.api)
    else:
        summarize(args.samples, args.output)


if __name__ == "__main__":
    main()
