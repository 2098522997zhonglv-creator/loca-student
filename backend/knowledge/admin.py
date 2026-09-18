from django.contrib import admin
from .models import (
    KnowledgeBase,
    Document,
    DocumentChunk,
    QueryLog,
    DingTalkConfig,
    DingTalkSyncBinding,
)


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'creator', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'description', 'project__name']
    readonly_fields = ['id', 'created_at', 'updated_at']

    fieldsets = (
        ('基本信息', {
            'fields': ('name', 'description', 'project', 'creator', 'is_active')
        }),
        ('分块配置', {
            'fields': ('chunk_size', 'chunk_overlap')
        }),
        ('系统信息', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'knowledge_base', 'document_type', 'status',
        'is_archived', 'uploader', 'uploaded_at',
    ]
    list_filter = ['document_type', 'status', 'is_archived', 'uploaded_at']
    search_fields = ['title', 'knowledge_base__name', 'external_id']
    readonly_fields = [
        'id', 'file_size', 'page_count', 'word_count',
        'uploaded_at', 'processed_at',
    ]

    fieldsets = (
        ('基本信息', {
            'fields': ('title', 'knowledge_base', 'document_type', 'uploader', 'is_archived')
        }),
        ('文件信息', {
            'fields': ('file', 'url', 'content')
        }),
        ('外部来源', {
            'fields': (
                'external_id', 'external_workspace_id', 'external_url',
                'external_path', 'external_modified_at',
            ),
            'classes': ('collapse',),
        }),
        ('处理状态', {
            'fields': ('status', 'error_message')
        }),
        ('元数据', {
            'fields': ('file_size', 'page_count', 'word_count'),
            'classes': ('collapse',)
        }),
        ('系统信息', {
            'fields': ('id', 'uploaded_at', 'processed_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ['document', 'chunk_index', 'vector_id', 'created_at']
    list_filter = ['created_at']
    search_fields = ['document__title', 'content']
    readonly_fields = ['id', 'created_at']


@admin.register(QueryLog)
class QueryLogAdmin(admin.ModelAdmin):
    list_display = ['knowledge_base', 'user', 'query_preview', 'total_time', 'created_at']
    list_filter = ['knowledge_base', 'created_at']
    search_fields = ['query', 'response']
    readonly_fields = ['id', 'created_at']

    def query_preview(self, obj):
        return obj.query[:50] + '...' if len(obj.query) > 50 else obj.query
    query_preview.short_description = '查询预览'


@admin.register(DingTalkConfig)
class DingTalkConfigAdmin(admin.ModelAdmin):
    list_display = ['enabled', 'app_key', 'operator_user_id', 'updated_at']
    readonly_fields = ['access_token', 'access_token_expires_at', 'operator_union_id', 'updated_at']


@admin.register(DingTalkSyncBinding)
class DingTalkSyncBindingAdmin(admin.ModelAdmin):
    list_display = [
        'knowledge_base', 'workspace_name', 'workspace_id',
        'enabled', 'interval_minutes', 'last_status', 'last_synced_at',
    ]
    list_filter = ['enabled', 'last_status']
    search_fields = ['workspace_id', 'workspace_name', 'knowledge_base__name']
