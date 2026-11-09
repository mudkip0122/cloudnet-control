"""A-only AWS CloudShell setup. Preview by default; --apply changes AWS settings.

Uses CloudShell's AWS CLI authentication, never requests or stores access keys.
Only dedicated single-group CloudNet deployments are changed automatically.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

INSTANCE = "i-0d5ce99f9135b4eea"
REGION = "ap-southeast-2"
PUBLIC_IP = "13.238.97.144"
BUDGET_NAME = "CloudNet-Control-Monthly"


def cidr(value):
    return str(ipaddress.IPv4Address(value)) + "/32"


def desired_rules(admin_ip, client_ips):
    return {(22, cidr(admin_ip))} | {(5000, cidr(ip)) for ip in [admin_ip, *client_ips]}


def rule_plan(rules, desired):
    """Reject overlapping ranges; preserve outbound and unrelated ports."""
    existing, revoke = set(), []
    for rule in rules:
        if rule.get("IsEgress"):
            continue
        protocol = str(rule["IpProtocol"])
        if protocol == "-1":
            raise ValueError("All-traffic inbound rule found; review it manually before applying")
        if protocol not in ("tcp", "6"):
            continue
        start, end = rule.get("FromPort", -1), rule.get("ToPort", -1)
        if not any(start <= port <= end for port in (22, 5000)):
            continue
        if start != end or start not in (22, 5000):
            raise ValueError("Overlapping TCP range found; review it manually before applying")
        key = (start, rule.get("CidrIpv4"))
        if key in desired:
            existing.add(key)
        else:
            revoke.append(rule["SecurityGroupRuleId"])
    return sorted(desired - existing), revoke


def aws(service, action, **parameters):
    args = ["aws", service, action, "--region", "us-east-1" if service == "budgets" else REGION,
            "--output", "json", "--no-cli-pager"]
    for key, value in parameters.items():
        args.append("--" + key.replace("_", "-"))
        if isinstance(value, (dict, list)):
            args.append(json.dumps(value))
        else:
            args.append(str(value))
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def notifications():
    return [{"NotificationType": kind, "ComparisonOperator": "GREATER_THAN",
             "Threshold": threshold, "ThresholdType": "PERCENTAGE"}
            for kind, threshold in (("ACTUAL", 80), ("ACTUAL", 100), ("FORECASTED", 100))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-ip", required=True)
    parser.add_argument("--client-ip", action="append", required=True)
    parser.add_argument("--monthly-usd", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        desired = desired_rules(args.admin_ip, args.client_ip)
        amount = Decimal(args.monthly_usd)
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Budget amount must be positive and finite")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.email):
            raise ValueError("Enter a valid notification email")
    except (ValueError, InvalidOperation) as exc:
        parser.error(str(exc))
    account = aws("sts", "get-caller-identity")["Account"]
    instance = aws("ec2", "describe-instances", instance_ids=INSTANCE)["Reservations"][0]["Instances"][0]
    if instance.get("PublicIpAddress") != PUBLIC_IP:
        raise ValueError("Instance public IP changed; refusing to apply stale settings")
    addresses = aws("ec2", "describe-addresses", public_ips=PUBLIC_IP)["Addresses"]
    if not any(a.get("InstanceId") == INSTANCE for a in addresses):
        raise ValueError("Elastic IP association does not match the intended instance")
    groups = instance["SecurityGroups"]
    if len(groups) != 1:
        raise ValueError("Multiple attached groups require manual review; no changes made")
    group_id = groups[0]["GroupId"]
    interfaces = aws("ec2", "describe-network-interfaces",
                     filters=[{"Name": "group-id", "Values": [group_id]}])["NetworkInterfaces"]
    if any(eni.get("Attachment", {}).get("InstanceId") != INSTANCE for eni in interfaces):
        raise ValueError("Security group is shared with other resources; no changes made")
    group = aws("ec2", "describe-security-groups", group_ids=group_id)
    rules = aws("ec2", "describe-security-group-rules",
                filters=[{"Name": "group-id", "Values": [group_id]}])["SecurityGroupRules"]
    additions, removals = rule_plan(rules, desired)
    existing_budget = None
    try:
        existing_budget = aws("budgets", "describe-budget", account_id=account,
                              budget_name=BUDGET_NAME)["Budget"]
    except RuntimeError as exc:
        if "(NotFoundException)" not in str(exc):
            raise
    if existing_budget is not None:
        if (existing_budget.get("BudgetType") != "COST" or existing_budget.get("TimeUnit") != "MONTHLY"
                or existing_budget.get("BudgetLimit", {}).get("Unit") != "USD"
                or Decimal(existing_budget["BudgetLimit"]["Amount"]) != amount
                or existing_budget.get("CostFilters") or existing_budget.get("FilterExpression")):
            raise ValueError("Existing budget differs; it will not be overwritten")
    planned_alerts = []
    if existing_budget is not None:
        current = aws("budgets", "describe-notifications-for-budget", account_id=account,
                      budget_name=BUDGET_NAME).get("Notifications", [])
        for notification in notifications():
            found = next((n for n in current if all(n.get(k) == v for k, v in notification.items())), None)
            if found is None:
                planned_alerts.append(("create", notification))
            else:
                subscribers = aws("budgets", "describe-subscribers-for-notification", account_id=account,
                                  budget_name=BUDGET_NAME, notification=notification).get("Subscribers", [])
                if not any(s.get("SubscriptionType") == "EMAIL" and s.get("Address") == args.email for s in subscribers):
                    if len(subscribers) >= 10:
                        raise ValueError("Existing notification has no subscriber capacity")
                    planned_alerts.append(("subscribe", notification))
        if len(current) + sum(mode == "create" for mode, _ in planned_alerts) > 5:
            raise ValueError("Existing budget has no notification capacity; no changes made")
    os.umask(0o077)
    folder = Path.home() / ("cloudnet-week1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    folder.mkdir(mode=0o700)
    snapshot = {"instance": INSTANCE, "region": REGION, "group": group,
                "rules": rules, "addresses": addresses, "budget": existing_budget,
                "add": additions, "revoke": removals, "desired": sorted(desired)}
    (folder / "before.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print("Instance:", INSTANCE, "Elastic IP:", PUBLIC_IP, "Group:", group_id)
    print("ADD:", additions, "REVOKE:", removals)
    print("Budget scope: entire AWS account; MONTHLY USD", str(amount))
    print("Alerts: actual 80%, actual 100%, forecast 100%; recipient:", args.email)
    print("Backup:", folder)
    if not args.apply:
        print("PREVIEW ONLY. No AWS settings changed. Review then add --apply.")
        return
    # Establish narrow allow rules before removing wider rules.
    for port, source in additions:
        aws("ec2", "authorize-security-group-ingress", group_id=group_id,
            ip_permissions=[{"IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                             "IpRanges": [{"CidrIp": source, "Description": "CloudNet approved PC"}]}])
    for rule_id in removals:
        aws("ec2", "revoke-security-group-ingress", group_id=group_id, security_group_rule_ids=rule_id)
    after = aws("ec2", "describe-security-group-rules",
                filters=[{"Name": "group-id", "Values": [group_id]}])["SecurityGroupRules"]
    missing, excess = rule_plan(after, desired)
    if missing or excess:
        raise RuntimeError("Post-change security group verification failed; inspect backup")
    subscriber = {"SubscriptionType": "EMAIL", "Address": args.email}
    if existing_budget is None:
        aws("budgets", "create-budget", account_id=account,
            budget={"BudgetName": BUDGET_NAME, "BudgetType": "COST", "TimeUnit": "MONTHLY",
                    "BudgetLimit": {"Amount": str(amount), "Unit": "USD"}},
            notifications_with_subscribers=[{"Notification": n, "Subscribers": [subscriber]} for n in notifications()])
    else:
        for mode, notification in planned_alerts:
            if mode == "create":
                aws("budgets", "create-notification", account_id=account, budget_name=BUDGET_NAME,
                    notification=notification, subscribers=[subscriber])
            else:
                aws("budgets", "create-subscriber", account_id=account, budget_name=BUDGET_NAME,
                    notification=notification, subscriber=subscriber)
    verified_budget = aws("budgets", "describe-budget", account_id=account, budget_name=BUDGET_NAME)
    verified_alerts = aws("budgets", "describe-notifications-for-budget", account_id=account, budget_name=BUDGET_NAME)
    for notification in notifications():
        subscribers = aws("budgets", "describe-subscribers-for-notification", account_id=account,
                          budget_name=BUDGET_NAME, notification=notification)["Subscribers"]
        if subscriber not in subscribers:
            raise RuntimeError("Budget recipient verification failed")
    (folder / "after.json").write_text(json.dumps({"rules": after, "budget": verified_budget,
                                                  "notifications": verified_alerts}, indent=2), encoding="utf-8")
    print("APPLIED: restricted SSH/service access, verified Elastic IP and budget configuration.")
    print("Confirm the recipient verification email. Notification delivery has not been tested.")
    print("No instance stop, address release, server restart, certificate or Agent/GUI changes performed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
        raise SystemExit("Stopped: " + str(exc))
