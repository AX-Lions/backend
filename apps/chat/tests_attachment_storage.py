"""
첨부 파일을 실제로 저장하고 내려주는가 (「채팅에서 프론트가 못 붙이는 것들」 중 하나).

전에는 업로드한 파일을 **버리고** `url` 에 `/media/chat/...` 자리표시자만
넣었습니다. 눌러도 404 라 화면이 내려받기 링크를 못 만들고 파일 이름만
보여줬습니다.
"""
import shutil
import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import ChatAttachment, ChatRoom, RoomMember, RoomType
from apps.chat.services import direct_key

MEDIA = tempfile.mkdtemp(prefix="bordo-test-media-")


@override_settings(MEDIA_ROOT=MEDIA)
class AttachmentStorageTest(TestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.me = User.objects.create_user(email="u@bordo.dev", password="x" * 10,
                                           name="유수인")
        self.mate = User.objects.create_user(email="v@bordo.dev", password="x" * 10,
                                             name="최비성")
        self.outsider = User.objects.create_user(email="w@bordo.dev", password="x" * 10,
                                                 name="남의 사람")
        self.room = ChatRoom.objects.create(
            type=RoomType.DIRECT, dedupe_key=direct_key(self.me.id, self.mate.id),
            created_by=self.me)
        for u in (self.me, self.mate):
            RoomMember.objects.create(room=self.room, user=u)
        self.client = APIClient()
        self.client.force_authenticate(self.me)

    def upload(self, name="설계안.txt", body=b"hello", content_type="text/plain"):
        return self.client.post(
            f"/api/v1/chat/rooms/{self.room.id}/attachments",
            {"file": SimpleUploadedFile(name, body, content_type=content_type)},
            format="multipart")

    def test_file_is_actually_stored(self):
        r = self.upload()
        self.assertEqual(r.status_code, 201, r.data)
        att = ChatAttachment.objects.get(pk=r.data["id"])
        self.assertTrue(att.stored_path)
        self.assertTrue(default_storage.exists(att.stored_path))

    def test_url_points_at_the_download_view(self):
        """`/media/` 로 그냥 열면 주소만 아는 사람이 받습니다."""
        r = self.upload()
        self.assertEqual(r.data["url"],
                         f"/api/v1/chat/attachments/{r.data['id']}/download")

    def test_download_gives_the_bytes_back(self):
        att_id = self.upload(body=b"hello bordo").data["id"]
        r = self.client.get(f"/api/v1/chat/attachments/{att_id}/download")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), b"hello bordo")

    def test_original_name_is_kept_for_display(self):
        att = ChatAttachment.objects.get(pk=self.upload(name="설계안.txt").data["id"])
        self.assertEqual(att.name, "설계안.txt")

    def test_uploaded_name_is_not_used_as_the_path(self):
        """`../` 가 들어오면 저장소 밖으로 빠져나가고, 같은 이름은 앞의 것을 덮어씁니다."""
        att = ChatAttachment.objects.get(
            pk=self.upload(name="../../탈출.txt").data["id"])
        self.assertTrue(att.stored_path.startswith(f"chat/{self.room.id}/"))
        self.assertNotIn("..", att.stored_path)

    def test_same_name_twice_does_not_overwrite(self):
        a = ChatAttachment.objects.get(pk=self.upload(body="첫 번째".encode()).data["id"])
        b = ChatAttachment.objects.get(pk=self.upload(body="두 번째".encode()).data["id"])
        self.assertNotEqual(a.stored_path, b.stored_path)
        self.assertTrue(default_storage.exists(a.stored_path))

    def test_outsider_gets_404(self):
        """403 을 주면 그런 파일이 있긴 하다가 샙니다."""
        att_id = self.upload().data["id"]
        self.client.force_authenticate(self.outsider)
        r = self.client.get(f"/api/v1/chat/attachments/{att_id}/download")
        self.assertEqual(r.status_code, 404)

    def test_deleting_removes_the_file(self):
        """행만 지우면 디스크에 남아 주소를 아는 사람이 계속 받습니다."""
        att = ChatAttachment.objects.get(pk=self.upload().data["id"])
        path = att.stored_path
        r = self.client.delete(f"/api/v1/chat/attachments/{att.id}")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(default_storage.exists(path))

    @override_settings(CHAT_ATTACHMENT_MAX_BYTES=10)
    def test_too_big_is_rejected(self):
        """큰 파일 하나가 디스크를 채우면 그때부터 모든 업로드가 함께 실패합니다."""
        r = self.upload(body=b"x" * 100)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ChatAttachment.objects.exists())
