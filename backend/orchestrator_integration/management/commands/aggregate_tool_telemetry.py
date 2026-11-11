"""聚合工具调用观测：Top 误调、慢路径、分区命中。"""

from django.core.management.base import BaseCommand

from orchestrator_integration.tool_telemetry import aggregate_telemetry


class Command(BaseCommand):
    help = "聚合近期 tool_telemetry（内存环 + data/logs/tool_telemetry.jsonl）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=2000,
            help="最多聚合条数（默认 2000）",
        )

    def handle(self, *args, **options):
        summary = aggregate_telemetry(limit=int(options["limit"] or 2000))
        self.stdout.write(self.style.SUCCESS(f"total={summary.get('total', 0)}"))
        for key in (
            "governance_kpis",
            "zone_hits",
            "playbook_hits",
            "top_allowlist_rejects",
            "top_catalog_rejects",
            "error_kinds",
            "top_slow_calls",
        ):
            self.stdout.write(f"\n[{key}]")
            self.stdout.write(str(summary.get(key)))
