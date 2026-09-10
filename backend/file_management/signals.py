from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import FileReference, FileAsset
from .services import maybe_cleanup_unreferenced_files


@receiver(post_delete, sender='langgraph_integration.ChatSession')
def on_chat_session_deleted(sender, instance, **kwargs):
    FileReference.objects.filter(
        ref_type=FileReference.REF_LLM_CHAT,
        ref_id=getattr(instance, 'session_id', '')
    ).delete()


@receiver(post_delete, sender=FileReference)
def on_file_reference_deleted(sender, instance, **kwargs):
    file_asset = instance.file
    project = instance.project
    if FileAsset.objects.filter(id=file_asset.id).exists():
        maybe_cleanup_unreferenced_files(project, candidate_file_ids=[file_asset.id], reason='unbind')
        maybe_cleanup_unreferenced_files(project, candidate_file_ids=[file_asset.id], reason='zero_refs')
