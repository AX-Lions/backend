from rest_framework import serializers

from .models import Document, DocumentVersion


class DocumentSummarySerializer(serializers.ModelSerializer):
    owner_id = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()
    content_hash = serializers.CharField(source="hash", read_only=True)

    class Meta:
        model = Document
        fields = ("id", "project_id", "title", "category", "visibility",
                  "owner_id", "owner_name", "summary", "version", "content_hash",
                  "indexed", "updated_at")

    def get_owner_id(self, obj):
        return str(obj.owner_id) if obj.owner_id else None

    def get_owner_name(self, obj):
        return obj.owner.display_name if obj.owner_id else "(탈퇴한 사용자)"


class DocumentSerializer(DocumentSummarySerializer):
    class Meta(DocumentSummarySerializer.Meta):
        fields = DocumentSummarySerializer.Meta.fields + (
            "content", "sections", "delivery_context", "direction_label",
            "chunk_count", "masked_secrets", "created_at", "deleted_at")


class DocumentVersionSerializer(serializers.ModelSerializer):
    content_hash = serializers.CharField(source="hash", read_only=True)
    author_id = serializers.SerializerMethodField()

    class Meta:
        model = DocumentVersion
        fields = ("version", "title", "content_hash", "source", "author_id",
                  "restored_from", "reason", "created_at")

    def get_author_id(self, obj):
        return str(obj.author_id) if obj.author_id else None
