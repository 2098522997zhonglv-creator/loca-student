"""重建账号行为向量集合。"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from orchestrator_integration.behavior_memory import UserBehaviorStore
from orchestrator_integration.models import UserBehaviorEvent

User = get_user_model()


class Command(BaseCommand):
    help = "重建指定用户的账号行为向量（清空 Qdrant 集合后按 DB 事件重写）"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True, help="用户 ID")
        parser.add_argument(
            "--clear-only",
            action="store_true",
            help="仅删除向量集合，不重写",
        )

    def handle(self, *args, **options):
        user_id = options["user_id"]
        if not User.objects.filter(id=user_id).exists():
            raise CommandError(f"用户不存在: {user_id}")

        store = UserBehaviorStore()
        self.stdout.write(f"清理集合 user_behavior_{user_id} …")
        store.drop_collection(user_id)

        if options["clear_only"]:
            self.stdout.write(self.style.SUCCESS("已清空向量集合"))
            return

        events = UserBehaviorEvent.objects.filter(user_id=user_id).order_by("created_at")
        total = events.count()
        ok = 0
        for ev in events:
            try:
                # 重新写入：先删旧 vector_id 关联，再走 store.record 会新建 DB 行
                # 这里直接 upsert 向量，保留原事件
                from orchestrator_integration.behavior_memory import (
                    DENSE_VECTOR_NAME,
                    _ensure_collection,
                    _get_embeddings,
                    _get_qdrant_client,
                    collection_name_for_user,
                    sanitize_behavior_text,
                )
                from qdrant_client.models import PointStruct
                import uuid

                summary = sanitize_behavior_text(ev.summary_text)
                if not summary:
                    continue
                embeddings = _get_embeddings()
                vector = embeddings.embed_query(summary)
                client = _get_qdrant_client()
                collection = collection_name_for_user(user_id)
                _ensure_collection(client, collection, len(vector))
                vid = ev.vector_id or str(uuid.uuid4())
                if not ev.vector_id:
                    ev.vector_id = vid
                    ev.save(update_fields=["vector_id"])
                client.upsert(
                    collection_name=collection,
                    points=[
                        PointStruct(
                            id=vid,
                            vector={DENSE_VECTOR_NAME: vector},
                            payload={
                                "event_id": ev.id,
                                "event_type": ev.event_type,
                                "summary_text": summary,
                                "project_id": ev.project_id,
                                "user_id": user_id,
                                "metadata": ev.metadata or {},
                            },
                        )
                    ],
                )
                ok += 1
            except Exception as e:
                self.stderr.write(f"  事件 {ev.id} 失败: {e}")

        self.stdout.write(
            self.style.SUCCESS(f"重建完成: {ok}/{total} 条写入 user_behavior_{user_id}")
        )
