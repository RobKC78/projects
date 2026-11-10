import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

from .api import Api
from .engine import PendingChange, apply
from .model import KEY, ROOT, decode, load, require, validate


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, text=True, check=True, capture_output=True).stdout


def github_cli(*args):
    return subprocess.run(["gh", *args], cwd=ROOT, text=True, check=True, capture_output=True).stdout.strip()


def issue_key(value):
    require(isinstance(value, str) and re.fullmatch(KEY, value), "Invalid Jira key")
    return value


def jira():
    credentials = os.environ["JIRA_USER"] + ":" + os.environ["JIRA_TOKEN"]
    return Api(os.environ["JIRA_URL"], "Basic " + base64.b64encode(credentials.encode()).decode())


def field_text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return value["value"]
    require(isinstance(value, dict) and value.get("type") == "doc", "Unexpected Jira field format")
    lines = []
    for block in value["content"]:
        require(block["type"] == "paragraph", "Use plain text in JSON fields")
        pieces = []
        for node in block.get("content", []):
            require(node["type"] in {"text", "hardBreak"}, "Use plain text in JSON fields")
            pieces.append(node["text"] if node["type"] == "text" else "\n")
        lines.append("".join(pieces))
    return "\n".join(lines)


def jira_request(api, key, zones):
    config = load(ROOT / "config/jira.json")
    require(key.split("-")[0] == config["project_key"], "Issue is outside the configured Jira project")
    mapping = config["fields"]
    query = urlencode({"fields": ",".join(["summary", "project", "issuetype", "status", *mapping.values()])})
    issue = api.call("GET", f"/rest/api/3/issue/{key}?{query}")
    fields = issue["fields"]
    require(issue["key"] == key and fields["project"]["key"] == config["project_key"]
            and fields["issuetype"]["id"] == config["issue_type_id"]
            and fields["status"]["id"] == config["approved_status_id"],
            "Jira issue is outside the approved workflow state")
    values = {name: field_text(fields[field]).strip() for name, field in mapping.items()}
    window = None
    if values["window_start"] or values["window_end"]:
        window = {"start": values["window_start"], "end": values["window_end"]}
    req = {
        "issue_key": key, "summary": fields["summary"], "operation": values["operation"].lower(),
        "zone_key": values["zone_key"], "owner": values["owner"], "type": values["type"].upper(),
        "expected": decode(values["expected"]) if values["expected"] else None,
        "desired": decode(values["desired"]) if values["desired"] else None,
        "window": window,
    }
    return validate(req, zones)[0]


def comment(api, key, body):
    api.call("POST", f"/rest/servicedeskapi/request/{key}/comment", {"body": body, "public": False})


def intake(key, zones):
    api = jira()
    req = jira_request(api, key, zones)
    path = ROOT / "requests" / f"{key}.json"
    require(not path.exists(), "Ticket is already present on main; use a new request")
    branch = f"dns/{key}"
    existing = github_cli("pr", "list", "--state", "all", "--head", branch,
                          "--json", "url,state", "--limit", "2")
    prs = json.loads(existing)
    if prs:
        require(len(prs) == 1 and prs[0]["state"] == "OPEN",
                "This ticket already has a completed or closed PR; use a new ticket")
        comment(api, key, f"DNS GitOps pull request is already open. {prs[0]['url']}")
        return
    git("config", "user.name", "DNS GitOps")
    git("config", "user.email", "dns-gitops@users.noreply.github.com")
    git("switch", "-c", branch)
    path.write_text(json.dumps(req, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    git("add", "--", str(path))
    git("commit", "-m", f"DNS request {key}")
    # A successful push with a failed PR create can be retried. Only this bot may write dns/*.
    git("push", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}")
    url = github_cli("pr", "create", "--base", "main", "--head", branch, "--title",
                     f"{key} DNS {req['operation']}", "--body",
                     f"Approved Jira intake {key}. Review the before and after states in the request JSON.")
    comment(api, key, f"DNS GitOps pull request awaiting review. {url}")


def sha(value):
    require(re.fullmatch(r"[0-9a-f]{40}", value) and value != "0"*40, "Invalid Git commit SHA")
    return value


def delta(base, head, zones, required):
    lines = git("diff", "--name-status", "--no-renames", sha(base), sha(head)).splitlines()
    requests = [line for line in lines if line.split("\t")[-1].startswith("requests/")]
    if not requests and not required:
        return None
    require(len(lines) == len(requests) == 1, "Each DNS change must add exactly one request and no other files")
    action, name = requests[0].split("\t")
    match = re.fullmatch(rf"requests/({KEY})\.json", name)
    require(action == "A" and match, "DNS request files are append-only")
    path = ROOT / name
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
            "Invalid request file")
    req, zone = validate(load(path), zones)
    require(req["issue_key"] == match[1], "Jira key must match filename")
    return req, zone


def reject_superseded(head, req, zone, zones):
    latest = git("rev-parse", "origin/main").strip()
    require(subprocess.run(["git", "merge-base", "--is-ancestor", head, latest], cwd=ROOT).returncode == 0,
            "Deployment commit is outside main history")
    changed = git("diff", "--name-only", "--no-renames", head, latest, "--",
                  "requests/", "config/zones.json", "config/jira.json", "dnsgitops/",
                  ".github/workflows/deploy.yml").splitlines()
    for name in changed:
        require(name.startswith("requests/"), "Deployment configuration or code changed after this commit")
        content = git("show", f"{latest}:{name}")
        other = json.loads(content)
        other_zone = zones.get(other.get("zone_key"), {})
        require(other_zone.get("zone_id") != zone["zone_id"] or other.get("owner", "").lower().rstrip(".") != req["owner"],
                "A newer merged request exists for this DNS owner; reconcile in a new request")


def deploy(base, head, zones):
    req, zone = delta(base, head, zones, True)
    api = jira()
    key = req["issue_key"]
    run = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    try:
        reject_superseded(head, req, zone, zones)
        require(jira_request(api, key, zones) == req, "Jira inputs changed after pull request creation")
        result = apply(req, zone, PendingChange(os.environ["DNS_STATE_DIR"], key))
    except Exception:
        try:
            comment(api, key, f"DNS GitOps did not confirm completion. Check the run and BAM before retrying. {run}")
        except Exception:
            print("Jira failure comment could not be posted", file=sys.stderr)
        raise
    print(result)
    comment(api, key, f"DNS GitOps completed. {result}. {run}")


def main():
    zones = load(ROOT / "config/zones.json")
    cmd, *args = sys.argv[1:]
    if cmd == "intake":
        intake(issue_key(args[0]), zones)
    elif cmd == "check":
        result = delta(*args, zones, False)
        if result:
            req, _ = result
            print(json.dumps(req, indent=2))
    elif cmd == "deploy":
        deploy(*args, zones)
    else:
        raise ValueError("Unknown command")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        if isinstance(error, json.JSONDecodeError):
            print("DNS GitOps failed: invalid JSON", file=sys.stderr)
        else:
            print(f"DNS GitOps failed: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        details = str(error) if isinstance(error, subprocess.CalledProcessError) else type(error).__name__
        print(f"DNS GitOps failed: {details}; inspect the operation and credentials", file=sys.stderr)
        sys.exit(1)
