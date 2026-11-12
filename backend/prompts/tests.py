from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import UserPrompt


def _grant_prompt_perms(user: User) -> None:
    ct = ContentType.objects.get_for_model(UserPrompt)
    perms = Permission.objects.filter(content_type=ct)
    user.user_permissions.add(*perms)


class UserPromptModelTests(TestCase):
    """用户提示词模型测试"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )
        # signals 会自动初始化默认提示词；再追加测试用记录
        self.default_prompt = (
            UserPrompt.objects.filter(user=self.user, is_default=True).first()
            or UserPrompt.objects.create(
                user=self.user,
                name="默认提示词",
                content="这是默认提示词内容",
                description="默认提示词描述",
                is_default=True,
                is_active=True,
            )
        )
        self.normal_prompt = UserPrompt.objects.create(
            user=self.user,
            name="普通提示词-测试专用",
            content="这是普通提示词内容",
            description="普通提示词描述",
            is_default=False,
            is_active=True,
        )

    def test_default_prompt_uniqueness(self):
        new_prompt = UserPrompt.objects.create(
            user=self.user,
            name="另一个默认提示词",
            content="另一个默认提示词内容",
            is_default=True,
            is_active=True,
        )
        self.default_prompt.refresh_from_db()
        self.assertFalse(self.default_prompt.is_default)
        self.assertTrue(new_prompt.is_default)

    def test_get_user_default_prompt(self):
        default_prompt = UserPrompt.get_user_default_prompt(self.user)
        self.assertEqual(default_prompt, self.default_prompt)

    def test_get_user_prompts(self):
        prompts = UserPrompt.get_user_prompts(self.user)
        # 含信号初始化默认集 + setUp 追加项
        self.assertGreaterEqual(prompts.count(), 2)
        self.assertTrue(prompts.filter(id=self.normal_prompt.id).exists())


class UserPromptAPITests(TestCase):
    """用户提示词API测试"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )
        _grant_prompt_perms(self.user)
        self.default_prompt = (
            UserPrompt.objects.filter(user=self.user, is_default=True).first()
            or UserPrompt.objects.create(
                user=self.user,
                name="默认提示词",
                content="这是默认提示词内容",
                description="默认提示词描述",
                is_default=True,
                is_active=True,
            )
        )
        self.normal_prompt = UserPrompt.objects.create(
            user=self.user,
            name="普通提示词-测试专用",
            content="这是普通提示词内容",
            description="普通提示词描述",
            is_default=False,
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)
        self.baseline_count = UserPrompt.objects.filter(user=self.user).count()

    def test_list_prompts(self):
        url = "/api/prompts/user-prompts/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        if isinstance(data, dict) and "results" in data:
            self.assertGreaterEqual(len(data["results"]), 2)
        elif isinstance(data, dict) and "data" in data:
            payload = data["data"]
            items = payload["results"] if isinstance(payload, dict) else payload
            self.assertGreaterEqual(len(items), 2)
        else:
            self.assertGreaterEqual(len(data), 2)

    def test_get_default_prompt(self):
        url = "/api/prompts/user-prompts/default/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        if isinstance(response.data, dict) and "data" in response.data:
            self.assertEqual(response.data["data"]["id"], self.default_prompt.id)
        else:
            self.assertEqual(response.data["id"], self.default_prompt.id)

    def test_create_prompt(self):
        url = "/api/prompts/user-prompts/"
        data = {
            "name": "新提示词",
            "content": "这是新提示词内容，长度需要满足校验最少十字。",
            "description": "新提示词描述",
            "is_default": False,
            "is_active": True,
            "prompt_type": "general",
        }
        response = self.client.post(url, data, format="json")
        if response.status_code != status.HTTP_201_CREATED:
            self.fail(f"create prompt failed: {response.status_code} {response.data}")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            UserPrompt.objects.filter(user=self.user).count(),
            self.baseline_count + 1,
        )

    def test_set_default_prompt(self):
        url = f"/api/prompts/user-prompts/{self.normal_prompt.id}/set_default/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.normal_prompt.refresh_from_db()
        self.default_prompt.refresh_from_db()
        self.assertTrue(self.normal_prompt.is_default)
        self.assertFalse(self.default_prompt.is_default)

    def test_duplicate_prompt(self):
        url = f"/api/prompts/user-prompts/{self.default_prompt.id}/duplicate/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            UserPrompt.objects.filter(user=self.user).count(),
            self.baseline_count + 1,
        )
        duplicate_name = f"{self.default_prompt.name} (副本)"
        self.assertTrue(UserPrompt.objects.filter(name=duplicate_name).exists())
