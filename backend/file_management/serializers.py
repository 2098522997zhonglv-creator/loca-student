from rest_framework import serializers
from .models import FileAsset, FileReference, FileManagementSetting
from .services import DEFAULT_MAX_FILE_SIZE, calculate_sha256


class FileAssetSerializer(serializers.ModelSerializer):
    file_id = serializers.IntegerField(source='id', read_only=True)
    name = serializers.CharField(source='original_name', read_only=True)
    url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    reference_count = serializers.SerializerMethodField()

    class Meta:
        model = FileAsset
        fields = [
            'id', 'file_id', 'name', 'original_name', 'extension', 'mime_type', 'size',
            'sha256', 'status', 'is_deleted', 'url', 'download_url', 'reference_count',
            'created_at', 'updated_at', 'owner', 'project'
        ]
        read_only_fields = fields

    def get_url(self, obj):
        request = self.context.get('request')
        if not obj.file:
            return ''
        return request.build_absolute_uri(obj.file.url) if request else obj.file.url

    def get_download_url(self, obj):
        request = self.context.get('request')
        if not request:
            return ''
        view = self.context.get('view')
        project_pk = getattr(view, 'kwargs', {}).get('project_pk') if view else obj.project_id
        return request.build_absolute_uri(f'/api/projects/{project_pk}/files/{obj.id}/download/')

    def get_reference_count(self, obj):
        return obj.references.count()


class FileUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        max_size = DEFAULT_MAX_FILE_SIZE
        if value.size > max_size:
            raise serializers.ValidationError(f'单文件大小不能超过 {max_size // 1024 // 1024}MB。')
        return value

    def create(self, validated_data):
        request = self.context['request']
        project = self.context['project']
        uploaded_file = validated_data['file']
        sha256 = calculate_sha256(uploaded_file)
        asset = FileAsset.objects.create(
            project=project,
            owner=request.user,
            file=uploaded_file,
            original_name=uploaded_file.name,
            extension='.' + uploaded_file.name.rsplit('.', 1)[-1].lower() if '.' in uploaded_file.name else '',
            mime_type=getattr(uploaded_file, 'content_type', '') or '',
            size=uploaded_file.size,
            sha256=sha256,
        )
        return asset


class FileReferenceSerializer(serializers.ModelSerializer):
    file = FileAssetSerializer(read_only=True)

    class Meta:
        model = FileReference
        fields = '__all__'
        read_only_fields = fields



class FileManagementSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileManagementSetting
        fields = ['auto_delete_on_unbind', 'auto_delete_zero_refs', 'updated_at']
        read_only_fields = ['updated_at']
