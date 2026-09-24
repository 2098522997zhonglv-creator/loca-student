from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import KnowledgeGlobalConfig
from .views import _mask_secret


class KnowledgeGlobalConfigSecretHandlingTests(TestCase):
    """验证全局配置中的脱敏密钥不会在保存或测试时被破坏。"""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="testpass123",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin)
        self.config = KnowledgeGlobalConfig.get_config()
        self.config.embedding_service = "custom"
        self.config.api_base_url = "https://integrate.api.nvidia.com/v1/embeddings"
        self.config.api_key = "nvapi-real-secret-NvRS"
        self.config.model_name = "baai/bge-m3"
        self.config.reranker_service = "custom"
        self.config.reranker_api_url = "https://reranker.example.com/v1/rerank"
        self.config.reranker_api_key = "reranker-real-secret"
        self.config.reranker_model_name = "Qwen3-VL-Reranker-2B"
        self.config.save()

    def test_put_global_config_keeps_real_secret_when_api_key_is_omitted(self):
        payload = {
            "embedding_service": "custom",
            "api_base_url": "https://integrate.api.nvidia.com/v1/embeddings",
            "model_name": "baai/bge-m3",
            "reranker_service": "custom",
            "reranker_api_url": "https://reranker.example.com/v1/rerank",
            "reranker_model_name": "Qwen3-VL-Reranker-2B",
            "chunk_size": 1200,
            "chunk_overlap": 150,
        }

        update_response = self.client.put(
            "/api/knowledge/global-config/", payload, format="json"
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        self.config.refresh_from_db()
        self.assertEqual(self.config.api_key, "nvapi-real-secret-NvRS")
        self.assertEqual(self.config.reranker_api_key, "reranker-real-secret")
        self.assertEqual(self.config.chunk_size, 1200)
        self.assertEqual(self.config.chunk_overlap, 150)

    @patch("requests.Session.post")
    def test_embedding_connection_uses_stored_secret_when_api_key_is_omitted(
        self, mock_post
    ):
        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/api/knowledge/test-embedding-connection/",
            {
                "embedding_service": "custom",
                "api_base_url": "https://integrate.api.nvidia.com/v1/embeddings",
                "model_name": "baai/bge-m3",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json().get("data", response.json())
        self.assertEqual(payload["success"], True)
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer nvapi-real-secret-NvRS",
        )

    def test_put_global_config_keeps_real_secret_when_masked_value_is_sent_back(self):
        response = self.client.get("/api/knowledge/global-config/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        payload = response.json()
        payload["chunk_size"] = 1300
        payload["chunk_overlap"] = 160

        update_response = self.client.put(
            "/api/knowledge/global-config/", payload, format="json"
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)

        self.config.refresh_from_db()
        self.assertEqual(self.config.api_key, "nvapi-real-secret-NvRS")
        self.assertEqual(self.config.reranker_api_key, "reranker-real-secret")
        self.assertEqual(self.config.chunk_size, 1300)
        self.assertEqual(self.config.chunk_overlap, 160)


class SemanticRerankerEndpointTests(TestCase):
    """外接开源语义重排：URL 解析与响应归一化。"""

    def test_resolve_xinference_and_openai_compatible_urls(self):
        from .reranker import resolve_reranker_endpoint

        xi = resolve_reranker_endpoint(
            service="xinference",
            api_url="http://127.0.0.1:9997",
            model_name="bge-reranker-v2-m3",
        )
        self.assertIsNotNone(xi)
        self.assertEqual(xi.url, "http://127.0.0.1:9997/v1/rerank")
        self.assertEqual(xi.service, "xinference")

        full = resolve_reranker_endpoint(
            service="openai_compatible",
            api_url="http://host:8001/v1/rerank",
            model_name="bge-reranker-v2-m3",
        )
        self.assertEqual(full.url, "http://host:8001/v1/rerank")

        custom = resolve_reranker_endpoint(
            service="custom",
            api_url="http://host:8080",
            model_name="",
        )
        self.assertEqual(custom.service, "openai_compatible")
        self.assertEqual(custom.model, "bge-reranker-v2-m3")
        self.assertEqual(custom.url, "http://host:8080/v1/rerank")

    def test_resolve_tei_jina_cohere_urls(self):
        from .reranker import resolve_reranker_endpoint

        tei = resolve_reranker_endpoint(
            service="tei",
            api_url="http://127.0.0.1:8080",
            model_name="BAAI/bge-reranker-v2-m3",
        )
        self.assertEqual(tei.url, "http://127.0.0.1:8080/rerank")
        self.assertEqual(tei.service, "tei")

        jina = resolve_reranker_endpoint(
            service="jina",
            api_url="https://api.jina.ai",
            model_name="jina-reranker-v2-base-multilingual",
        )
        self.assertEqual(jina.url, "https://api.jina.ai/v1/rerank")

        cohere = resolve_reranker_endpoint(
            service="cohere",
            api_url="https://api.cohere.com",
            model_name="rerank-multilingual-v3.0",
        )
        self.assertEqual(cohere.url, "https://api.cohere.com/v1/rerank")

    def test_none_and_missing_url(self):
        from .reranker import resolve_reranker_endpoint

        self.assertIsNone(
            resolve_reranker_endpoint(service="none", api_url="", model_name="")
        )
        self.assertIsNone(
            resolve_reranker_endpoint(
                service="openai_compatible", api_url="", model_name="m"
            )
        )
        # xinference 可回退默认本机地址
        xi = resolve_reranker_endpoint(
            service="xinference", api_url="", model_name="m"
        )
        self.assertEqual(xi.url, "http://localhost:9997/v1/rerank")

    def test_extract_scored_indices_formats(self):
        from .reranker import _extract_scored_indices

        tei = _extract_scored_indices(
            [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}], 2
        )
        self.assertEqual(tei, [(1, 0.9), (0, 0.2)])

        openai_style = _extract_scored_indices(
            {"results": [{"index": 0, "relevance_score": 0.8}, {"index": 1, "score": 0.1}]},
            2,
        )
        self.assertEqual(openai_style, [(0, 0.8), (1, 0.1)])

        aligned = _extract_scored_indices({"scores": [0.1, 0.7, 0.3]}, 3)
        self.assertEqual(aligned, [(0, 0.1), (1, 0.7), (2, 0.3)])

    @patch("requests.Session.post")
    def test_semantic_reranker_posts_tei_body(self, mock_post):
        from .reranker import RerankerEndpoint, SemanticReranker

        mock_response = Mock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"index": 0, "score": 0.95},
            {"index": 1, "score": 0.1},
        ]
        mock_post.return_value = mock_response

        client = SemanticReranker(
            RerankerEndpoint(
                service="tei",
                url="http://127.0.0.1:8080/rerank",
                model="BAAI/bge-reranker-v2-m3",
            )
        )
        scored = client.rerank_texts("q", ["doc-a", "doc-b"], top_n=1)
        self.assertEqual(scored, [(0, 0.95)])
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["texts"], ["doc-a", "doc-b"])
        self.assertNotIn("documents", kwargs["json"])


class Bm25ChinesePreprocessTests(TestCase):
    """BM25 中文预处理：连续中文需切开，否则稀疏 overlap 为 0。"""

    def test_pure_ascii_unchanged(self):
        from .services import preprocess_text_for_bm25

        text = "Callie release callie_test"
        self.assertEqual(preprocess_text_for_bm25(text), text)

    def test_chinese_query_is_tokenized_with_spaces(self):
        from .services import preprocess_text_for_bm25

        prepared = preprocess_text_for_bm25("Callieus生产地址")
        parts = [p for p in prepared.split() if p]
        self.assertGreaterEqual(len(parts), 2, prepared)
        # 至少应拆出可与文档共享的中文词/字
        joined = "".join(parts)
        self.assertIn("生产", joined)
        self.assertIn("地址", joined)

    def test_query_and_doc_share_tokens_after_preprocess(self):
        from .services import preprocess_text_for_bm25

        q = set(preprocess_text_for_bm25("Callieus生产地址").split())
        d = set(
            preprocess_text_for_bm25(
                "Callie前后端分离 | 各环境配置地址 | 生产环境"
            ).split()
        )
        self.assertTrue(q & d, f"expected overlap, q={q}, d={d}")
