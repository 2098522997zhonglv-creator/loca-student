from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from prompts.services import get_default_prompts, initialize_user_prompts


class Command(BaseCommand):
    help = (
        "从 prompts.default_templates（与 WHartTest 同源）为用户初始化默认提示词。"
        "包含：默认通用提示词、六维评审分析、测试用例执行、智能用例生成、图表生成。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=int,
            help="指定用户 ID；不传则默认第一个超级用户",
        )
        parser.add_argument(
            "--all-users",
            action="store_true",
            help="为所有用户初始化提示词",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="强制用模板覆盖已存在的同名/同类型提示词",
        )
        parser.add_argument(
            "--language",
            choices=["zh", "en"],
            default="zh",
            help="模板语言（默认 zh）",
        )

    def handle(self, *args, **options):
        language = options["language"]
        force_update = options["force"]
        templates = get_default_prompts(language)
        self.stdout.write(
            f"模板来源: prompts.default_templates/{language}.py （共 {len(templates)} 条）"
        )

        if options["all_users"]:
            users = list(User.objects.order_by("id"))
            if not users:
                self.stdout.write(self.style.ERROR("系统中没有任何用户"))
                return
        elif options.get("user"):
            try:
                users = [User.objects.get(id=options["user"])]
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"用户 ID={options['user']} 不存在"))
                return
        else:
            user = User.objects.filter(is_superuser=True).order_by("id").first()
            if not user:
                self.stdout.write(
                    self.style.ERROR("未找到超级用户，请先创建或使用 --user / --all-users")
                )
                return
            users = [user]

        for user in users:
            self.stdout.write(f"→ 初始化用户 {user.username} (id={user.id})")
            result = initialize_user_prompts(
                user, force_update=force_update, language=language
            )
            summary = result.get("summary", {})
            self.stdout.write(
                self.style.SUCCESS(
                    f"  created/updated={summary.get('created_count', 0)} "
                    f"skipped={summary.get('skipped_count', 0)} "
                    f"deleted={summary.get('deleted_count', 0)}"
                )
            )
            for item in result.get("created", []):
                action = item.get("action", "created")
                self.stdout.write(f"    [{action}] {item.get('name')} ({item.get('prompt_type')})")
            for item in result.get("skipped", [])[:5]:
                self.stdout.write(f"    [skipped] {item.get('name')}")
            if len(result.get("skipped", [])) > 5:
                self.stdout.write(f"    ... 另有 {len(result['skipped']) - 5} 条跳过")

        self.stdout.write(self.style.SUCCESS("默认提示词初始化完成。"))
        if not force_update:
            self.stdout.write(
                "提示：若需用 WHartTest 模板覆盖已有内容，请追加 --force"
            )
