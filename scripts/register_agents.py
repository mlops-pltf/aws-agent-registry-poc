#!/usr/bin/env python3
"""Register (or update) the three A2A agents in AWS Agent Registry.

For each agent it:
  1. resolves the AgentCore runtime ARN (given explicitly, or found by runtime name),
  2. fills the runtime URL into the agent card from registry-cards/,
  3. creates the AGENT record, or updates it if a record with that name exists,
  4. submits it for approval, and with --approve also approves it.

Safe to re-run: existing records are updated in place, not duplicated.

Examples:
  # runtimes found automatically by name (artist / investment_banker / fitness_trainer)
  python scripts/register_agents.py --registry my-registry --approve

  # explicit runtime ARNs or runtime names
  python scripts/register_agents.py --registry my-registry \\
      --runtime artist=arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/artist-AbC123 \\
      --runtime investment_banker=investment_banker_agent \\
      --runtime fitness_trainer=fitness_trainer_agent

  # print what would be registered without changing anything
  python scripts/register_agents.py --registry my-registry --dry-run
"""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import boto3

CARDS_DIR = Path(__file__).resolve().parent.parent / "registry-cards"

# agent key -> registry record name
AGENTS = {
    "artist": "artist-agent",
    "investment_banker": "investment-banker-agent",
    "fitness_trainer": "fitness-trainer-agent",
}

A2A_SCHEMA_VERSION = "0.3"
BUSY = {"CREATING", "UPDATING"}
FAILED = {"CREATE_FAILED", "UPDATE_FAILED"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="non-prod-admin", help="AWS profile (default: non-prod-admin)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--registry", required=True, help="Registry ID, ARN or name")
    parser.add_argument("--runtime", action="append", default=[], metavar="AGENT=ARN_OR_NAME",
                        help=f"Runtime for an agent; AGENT is one of {', '.join(AGENTS)}. Repeatable.")
    parser.add_argument("--only", choices=list(AGENTS), action="append",
                        help="Register only these agents. Repeatable.")
    parser.add_argument("--approve", action="store_true",
                        help="Also approve records left in PENDING_APPROVAL (needs curator permissions)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be registered and stop")
    return parser.parse_args()


def resolve_registry(control, registry: str) -> str:
    token = None
    while True:
        page = control.list_registries(**({"nextToken": token} if token else {}))
        for item in page["registries"]:
            if registry in (item["registryId"], item["registryArn"], item["name"]):
                return item["registryId"]
        token = page.get("nextToken")
        if not token:
            sys.exit(f"Registry '{registry}' not found in this account/region.")


def list_runtimes(agentcore_control) -> list[dict]:
    runtimes, token = [], None
    while True:
        page = agentcore_control.list_agent_runtimes(**({"nextToken": token} if token else {}))
        runtimes += page["agentRuntimes"]
        token = page.get("nextToken")
        if not token:
            return runtimes


def resolve_runtime_arn(key: str, value: str | None, runtimes: list[dict]) -> str:
    if value and value.startswith("arn:"):
        return value
    if value:
        matches = [r for r in runtimes if r["agentRuntimeName"] == value]
    else:
        matches = [r for r in runtimes if key.replace("_", "").lower() in r["agentRuntimeName"].replace("_", "").replace("-", "").lower()]
    if len(matches) != 1:
        names = ", ".join(r["agentRuntimeName"] for r in matches) or "none"
        sys.exit(f"Could not pick one runtime for '{key}' (matches: {names}). "
                 f"Pass --runtime {key}=<runtime ARN or exact name>.")
    return matches[0]["agentRuntimeArn"]


def build_card(key: str, runtime_arn: str) -> dict:
    card = json.loads((CARDS_DIR / f"{key}.agent-card.json").read_text())
    region = runtime_arn.split(":")[3]
    card["url"] = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(runtime_arn, safe='')}/invocations/"
    return card


def find_record(control, registry_id: str, name: str) -> dict | None:
    token = None
    while True:
        kwargs = {"registryId": registry_id, "filters": [{"name": "name", "values": [name]}]}
        if token:
            kwargs["nextToken"] = token
        page = control.list_registry_records(**kwargs)
        for record in page["registryRecords"]:
            if record["name"] == name:
                return record
        token = page.get("nextToken")
        if not token:
            return None


def wait_until_settled(control, registry_id: str, record_id: str, timeout: int = 300) -> dict:
    deadline = time.time() + timeout
    while True:
        record = control.get_registry_record(registryId=registry_id, recordId=record_id)
        if record["status"] in FAILED:
            sys.exit(f"Record {record['name']} is {record['status']}: {record.get('statusReason', 'no reason given')}")
        if record["status"] not in BUSY:
            return record
        if time.time() > deadline:
            sys.exit(f"Timed out waiting for record {record['name']} (still {record['status']}).")
        time.sleep(5)


def register(control, registry_id: str, name: str, card: dict, approve: bool) -> None:
    card_json = json.dumps(card)
    existing = find_record(control, registry_id, name)
    if existing:
        record_id = existing["recordId"]
        print(f"  updating existing record {record_id} (was {existing['status']})")
        control.update_registry_record(
            registryId=registry_id,
            recordId=record_id,
            description={"optionalValue": card["description"]},
            descriptors={"optionalValue": {"a2aAgentCard": {"optionalValue": {
                "data": {"optionalValue": card_json},
                "dataSchemaVersion": {"optionalValue": A2A_SCHEMA_VERSION},
            }}}},
        )
    else:
        response = control.create_registry_record(
            registryId=registry_id,
            name=name,
            displayName=card["name"],
            description=card["description"],
            recordType="AGENT",
            descriptors={"a2aAgentCard": {"data": card_json, "dataSchemaVersion": A2A_SCHEMA_VERSION}},
            recordVersion=card.get("version", "1.0.0"),
        )
        record_id = find_record(control, registry_id, name)["recordId"]
        print(f"  created record {record_id} ({response['recordArn']})")

    record = wait_until_settled(control, registry_id, record_id)
    if record["status"] == "DRAFT":
        record = control.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
        print(f"  submitted for approval -> {record['status']}")
    if record["status"] == "PENDING_APPROVAL" and approve:
        record = control.update_registry_record_status(
            registryId=registry_id, recordId=record_id, status="APPROVED",
            statusReason="Approved by register_agents.py",
        )
        print("  approved")
    print(f"  status: {record['status']}"
          + ("  (approve it in the console, or re-run with --approve)" if record["status"] == "PENDING_APPROVAL" else ""))


def main() -> None:
    args = parse_args()
    overrides = {}
    for item in args.runtime:
        key, sep, value = item.partition("=")
        if not sep or key not in AGENTS:
            sys.exit(f"Bad --runtime '{item}'. Use AGENT=ARN_OR_NAME with AGENT in {', '.join(AGENTS)}.")
        overrides[key] = value
    selected = args.only or list(AGENTS)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    print(f"Account {session.client('sts').get_caller_identity()['Account']} · profile {args.profile} · {args.region}")
    control = session.client("agent-registry-control")
    registry_id = resolve_registry(control, args.registry)

    need_lookup = any(not overrides.get(k, "").startswith("arn:") for k in selected)
    runtimes = list_runtimes(session.client("bedrock-agentcore-control")) if need_lookup else []

    for key in selected:
        name = AGENTS[key]
        runtime_arn = resolve_runtime_arn(key, overrides.get(key), runtimes)
        card = build_card(key, runtime_arn)
        print(f"\n{card['name']}  ->  record '{name}'\n  runtime {runtime_arn}")
        if args.dry_run:
            print(json.dumps(card, indent=2))
            continue
        register(control, registry_id, name, card, args.approve)

    print("\nDry run: nothing was changed." if args.dry_run else "\nDone.")


if __name__ == "__main__":
    main()
