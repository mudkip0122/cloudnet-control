"""Check that AWS setup cannot silently preserve broad SSH/service access."""
import unittest
from ops.aws_week1 import desired_rules, rule_plan


class SecurityGroupPlanTests(unittest.TestCase):
    def setUp(self):
        self.desired = desired_rules("203.0.113.10", ["198.51.100.20"])

    def rule(self, rule_id, port, source, **extra):
        return {"SecurityGroupRuleId": rule_id, "IpProtocol": "tcp", "FromPort": port,
                "ToPort": port, "CidrIpv4": source, "IsEgress": False, **extra}

    def test_add_narrow_rules_and_revoke_broad_ipv4_ipv6_and_group_sources(self):
        rules = [self.rule("ssh-world", 22, "0.0.0.0/0"),
                 self.rule("service-world6", 5000, None, CidrIpv6="::/0"),
                 self.rule("group-source", 22, None, ReferencedGroupInfo={"GroupId": "sg-other"}),
                 self.rule("existing", 5000, "198.51.100.20/32")]
        additions, revoke = rule_plan(rules, self.desired)
        self.assertEqual(set(additions), {(22, "203.0.113.10/32"), (5000, "203.0.113.10/32")})
        self.assertEqual(set(revoke), {"ssh-world", "service-world6", "group-source"})

    def test_never_revoke_unrelated_ports_or_outbound(self):
        rules = [self.rule("web", 443, "0.0.0.0/0"),
                 self.rule("outbound", 22, "0.0.0.0/0", IsEgress=True)]
        self.assertEqual(rule_plan(rules, self.desired)[1], [])

    def test_reject_all_traffic_and_overlapping_port_ranges(self):
        for rule in ({"IpProtocol": "-1", "IsEgress": False},
                     self.rule("range", 1, "0.0.0.0/0", ToPort=65535)):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                rule_plan([rule], self.desired)

    def test_exact_configuration_requires_no_mutations(self):
        rules = [self.rule(str(i), port, source) for i, (port, source) in enumerate(self.desired)]
        self.assertEqual(rule_plan(rules, self.desired), ([], []))

    def test_input_does_not_accept_broad_cidr_or_ipv6_as_client_ip(self):
        for value in ("0.0.0.0/0", "203.0.113.0/24", "::1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                desired_rules(value, [])
