"""超时回收悬挂任务 + 异常告警（阶段 5）。"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "回收租约过期/陈旧 running 任务，并输出运营异常告警"

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than",
            type=int,
            default=900,
            help="超过多少秒未更新视为悬挂（默认 900）",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=50,
            help="最多回收条数（默认 50）",
        )
        parser.add_argument(
            "--no-alerts",
            action="store_true",
            help="仅回收，不发出异常告警",
        )

    def handle(self, *args, **options):
        from orchestrator_integration.production_observability import (
            compute_ops_metrics,
            reclaim_stale_tasks,
        )

        older = int(options.get("older_than") or 900)
        limit = int(options.get("limit") or 50)
        result = reclaim_stale_tasks(
            older_than_seconds=older,
            limit=limit,
            emit_alerts=not bool(options.get("no_alerts")),
        )
        metrics = compute_ops_metrics(hanging_older_than_seconds=older)
        self.stdout.write(
            self.style.SUCCESS(
                f"reclaimed={result.get('reclaimed')} "
                f"hanging_before={result.get('hanging_before')} "
                f"hanging_now={metrics.get('hanging_task_count')}"
            )
        )
        self.stdout.write(
            "ops="
            + str(
                {
                    "completion_rate": metrics.get("completion_rate"),
                    "resume_success_rate": metrics.get("resume_success_rate"),
                    "duplicate_op_rate": metrics.get("duplicate_op_rate"),
                    "avg_duration_ms": metrics.get("avg_duration_ms"),
                }
            )
        )
        alerts = result.get("alerts") or []
        if alerts:
            self.stdout.write(self.style.WARNING(f"alerts={len(alerts)}"))
            for a in alerts:
                self.stdout.write(f"  - {a.get('code')}: {a.get('message')}")
        else:
            self.stdout.write("alerts=0")
