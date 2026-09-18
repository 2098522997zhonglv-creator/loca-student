from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from projects.models import Project
from testcases.models import TestCase as TestCaseModel, TestCaseModule, TestCaseStep


class TestCaseListApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="testuser",
            password="password",
            email="test@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.project = Project.objects.create(
            name="Test Project",
            description="Test Description",
            creator=self.user,
        )
        self.module = TestCaseModule.objects.create(
            project=self.project,
            name="Test Module",
            creator=self.user,
        )
        self.testcase = TestCaseModel.objects.create(
            project=self.project,
            module=self.module,
            name="Test Case 1",
            precondition="precondition",
            creator=self.user,
            review_status="pending_review",
        )
        TestCaseStep.objects.create(
            test_case=self.testcase,
            step_number=1,
            description="step description",
            expected_result="expected result",
            creator=self.user,
        )

    def test_list_api_does_not_return_steps(self):
        """列表接口只返回摘要信息，不应返回详情级 steps。"""
        url = f"/api/projects/{self.project.id}/testcases/"
        response = self.client.get(
            url,
            {
                "page": 1,
                "page_size": 10,
                "search": "",
                "review_status_in": "pending_review,approved,needs_optimization,optimization_pending_review",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, dict)
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertNotIn("steps", results[0])
        self.assertEqual(results[0]["id"], self.testcase.id)
        self.assertEqual(results[0]["name"], self.testcase.name)

    def test_list_api_uses_pagination(self):
        """列表接口应按 page/page_size 分页，返回 count/results 结构。"""
        for index in range(2, 13):
            TestCaseModel.objects.create(
                project=self.project,
                module=self.module,
                name=f"Test Case {index}",
                creator=self.user,
                review_status="pending_review",
            )

        url = f"/api/projects/{self.project.id}/testcases/"
        response = self.client.get(url, {"page": 1, "page_size": 10})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, dict)
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        self.assertEqual(response.data["count"], 12)
        self.assertEqual(len(response.data["results"]), 10)

    def test_list_api_can_include_steps_when_explicitly_requested(self):
        """脑图等完整数据场景可显式请求 steps，但普通列表默认不返回。"""
        url = f"/api/projects/{self.project.id}/testcases/"
        response = self.client.get(
            url,
            {
                "page": 1,
                "page_size": 10,
                "include_steps": "true",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertIn("steps", results[0])
        self.assertEqual(len(results[0]["steps"]), 1)
        self.assertEqual(results[0]["steps"][0]["description"], "step description")

    def test_retrieve_api_still_returns_steps(self):
        """详情接口仍返回 steps，避免影响用例编辑/查看详情。"""
        url = f"/api/projects/{self.project.id}/testcases/{self.testcase.id}/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("steps", response.data)
        self.assertEqual(len(response.data["steps"]), 1)
        self.assertEqual(response.data["steps"][0]["description"], "step description")

    def test_expected_result_is_optional(self):
        """用例步骤的预期结果应该是非必填的。"""
        # 1. 验证模型允许 expected_result 为空
        step = TestCaseStep.objects.create(
            test_case=self.testcase,
            step_number=2,
            description="another step description",
            creator=self.user,
        )
        self.assertEqual(step.expected_result, "")

        # 2. 验证序列化器/API 接口在未提供或提供空 expected_result 时能正常保存
        url = f"/api/projects/{self.project.id}/testcases/{self.testcase.id}/"
        payload = {
            "name": self.testcase.name,
            "level": self.testcase.level,
            "module_id": self.module.id,
            "steps": [
                {
                    "step_number": 1,
                    "description": "step 1",
                    "expected_result": "" # 预期结果为空字符串
                },
                {
                    "step_number": 2,
                    "description": "step 2" # 预期结果字段未提供
                }
            ]
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        steps_data = response.data["steps"]
        self.assertEqual(len(steps_data), 2)
        self.assertEqual(steps_data[0]["expected_result"], "")
        self.assertEqual(steps_data[1]["expected_result"], "")

