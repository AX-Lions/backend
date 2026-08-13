"""
문서.

**수정할 때마다 새 버전이 쌓입니다.** 덮어쓰지 않으므로 되짚을 수 있습니다.
`content` 는 원문 보존용으로 남기고, 화면은 `sections`(소제목 / 한 줄 요약 / 내용)
3단으로 그립니다.

`visibility=private` 은 소유자 외에게 **존재조차 숨깁니다.** 조회하면 403 이 아니라
404 입니다 — 403 을 주면 "그런 문서가 있긴 하다"는 정보가 새어 나갑니다.
"""
import hashlib
import re

from django.conf import settings
from django.db import models

from apps.common.models import SoftDeletable, TimeStamped, UUIDModel, Versioned


class Visibility(models.TextChoices):
    TEAM = "team", "팀 공개"
    PRIVATE = "private", "비공개"


#: 업로드 본문에서 지워야 할 것들. 실수로 올린 키가 벡터 인덱스까지 들어가면
#: 검색 결과와 대리인 발언에 그대로 실려 나갑니다.
SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9]{16,})"), "sk-***"),
    (re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{20,})"), "ghp_***"),
    (re.compile(r"(?i)\b(AKIA[0-9A-Z]{16})\b"), "AKIA***"),
    (re.compile(r"(?i)(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s\"']+"),
     r"\1://***"),
    (re.compile(r"(?i)((?:api[_-]?key|secret|password|token)\s*[:=]\s*)"
                r"[\"']?([A-Za-z0-9_\-\.]{8,})[\"']?"), r"\1***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?"
                r"-----END [A-Z ]*PRIVATE KEY-----"), "[PRIVATE KEY 제거됨]"),
]


def mask_secrets(text):
    """마스킹된 본문과 몇 건을 지웠는지 돌려줍니다."""
    if not text:
        return text or "", 0
    count = 0
    for pattern, repl in SECRET_PATTERNS:
        text, n = pattern.subn(repl, text)
        count += n
    return text, count


def content_hash(text):
    return "sha256:" + hashlib.sha256((text or "").encode()).hexdigest()[:8]


class Document(UUIDModel, TimeStamped, SoftDeletable, Versioned):
    project = models.ForeignKey("orgs.Project", on_delete=models.CASCADE,
                                related_name="documents")
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=40, blank=True, default="")
    visibility = models.CharField(max_length=10, choices=Visibility.choices,
                                  default=Visibility.TEAM)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, related_name="documents")

    content = models.TextField(blank=True, default="", help_text="원문 마크다운")
    summary = models.TextField(blank=True, default="",
                               help_text="검색 결과 미리보기. 서버가 채웁니다.")
    sections = models.JSONField(default=list, blank=True,
                                help_text="[{heading, one_line_summary, body}]")
    delivery_context = models.JSONField(
        default=list, blank=True,
        help_text="`문서 전달 맥락`. 이 문서가 오가게 된 대화 근거.")
    direction_label = models.CharField(max_length=200, blank=True, default="")

    hash = models.CharField(max_length=80, blank=True, default="", db_index=True)
    masked_secrets = models.PositiveSmallIntegerField(default=0)
    indexed = models.BooleanField(default=False,
                                  help_text="임베딩 색인 완료 여부. 2차에서 채웁니다.")
    chunk_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "document"
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["project", "category"]),
                   models.Index(fields=["project", "visibility"]),
                   models.Index(fields=["owner"])]

    def __str__(self):
        return self.title

    def visible_to(self, user):
        return self.visibility == Visibility.TEAM or self.owner_id == user.id


class DocumentVersion(models.Model):
    """
    버전 스냅샷.

    되돌리기는 **새 버전을 만드는 것**이지 덮어쓰는 게 아닙니다.
    동기화의 자동 병합 금지 원칙과 같은 맥락입니다.
    """
    class Source(models.TextChoices):
        WEB_EDIT = "web_edit", "웹 편집"
        MCP_PUSH = "mcp_push", "MCP 업로드"
        RESTORE = "restore", "버전 복원"

    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="versions")
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=200, blank=True, default="")
    content = models.TextField(blank=True, default="")
    sections = models.JSONField(default=list, blank=True)
    hash = models.CharField(max_length=80, blank=True, default="")
    source = models.CharField(max_length=10, choices=Source.choices,
                              default=Source.WEB_EDIT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, related_name="document_versions")
    restored_from = models.PositiveIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_version"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["document", "version"],
                                    name="uq_document_version"),
        ]
