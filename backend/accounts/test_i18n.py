from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from projects.models import Project, ProjectMember


class ApiLocalizationTests(APITestCase):
    def setUp(self):
        self.password = 'Secret123!'
        self.user = User.objects.create_user(username='i18n-user', password=self.password)
        self.superuser = User.objects.create_superuser(
            username='i18n-admin',
            email='admin@example.com',
            password=self.password,
        )

        self.project = Project.objects.create(
            name='i18n-project',
            description='demo',
            creator=self.superuser,
        )
        ProjectMember.objects.create(project=self.project, user=self.superuser, role='owner')

    def test_renderer_uses_chinese_by_default_for_validation_errors(self):
        response = self.client.post('/api/accounts/register/', {}, format='json')
        payload = response.json()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload['message'], '请求参数有误或处理失败')
        self.assertIn('username', payload['errors'])

    def test_login_error_is_translated_to_english(self):
        response = self.client.post(
            '/api/token/',
            {'username': self.user.username, 'password': 'wrong-password'},
            format='json',
            HTTP_ACCEPT_LANGUAGE='en',
        )
        payload = response.json()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(payload['message'], 'Invalid username or password')
        self.assertEqual(payload['errors']['detail'], 'Invalid username or password')

    def test_permission_error_respects_english_locale(self):
        self.client.force_authenticate(self.user)

        response = self.client.get('/api/projects/', HTTP_ACCEPT_LANGUAGE='en')
        payload = response.json()

        self.assertEqual(response.status_code, 403)
        self.assertIn('permission', payload['message'].lower())

    def test_success_response_uses_english_message(self):
        self.client.force_authenticate(self.superuser)

        response = self.client.get('/api/projects/', HTTP_ACCEPT_LANGUAGE='en')
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['message'], 'Request succeeded')
        self.assertIsInstance(payload['data'], list)
