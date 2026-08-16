"""
한 실행에 유보 질문 하나.

제약을 걸기 전에 **이미 있는 중복부터 풀어야 합니다.** 지금 데이터에는 중복이
없지만, 이 마이그레이션이 도는 시점에 하나라도 있으면 `AddConstraint` 가
`IntegrityError` 로 죽고 배포 전체가 멈춥니다. 막으려는 그 경합이 실제로
일어났던 환경일수록 그렇습니다.
"""
from django.conf import settings
from django.db import migrations, models


def unlink_duplicates(apps, schema_editor):
    """
    같은 실행에 달린 유보가 여럿이면 하나만 남기고 나머지는 실행 연결을 끊습니다.

    **지우지 않습니다.** 중복이라 해도 사용자가 이미 답을 달아 뒀을 수 있고,
    마이그레이션이 사람의 기록을 조용히 없애면 안 됩니다. `run` 만 비우면
    부분 유니크 제약(`run IS NOT NULL` 조건)에 걸리지 않으면서 행은 그대로
    남습니다 — 목록에도 계속 보입니다.

    남기는 것은 가장 먼저 만들어진 행입니다. 뒤엣것이 재시도로 생긴 사본이라
    앞엣것이 원본에 가깝습니다.
    """
    PendingQuestion = apps.get_model("agent", "PendingQuestion")

    seen = set()
    stale = []
    for pk, run_id in (PendingQuestion.objects
                       .filter(run__isnull=False)
                       .order_by("run_id", "created_at", "id")
                       .values_list("id", "run_id")):
        if run_id in seen:
            stale.append(pk)
        else:
            seen.add(run_id)

    if stale:
        PendingQuestion.objects.filter(id__in=stale).update(run=None)


class Migration(migrations.Migration):

    dependencies = [
        ('agent', '0002_outboxevent'),
        ('meetings', '0003_aibriefing_location_chips_briefingconfirmation_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 되돌리기는 아무것도 하지 않습니다. 끊어진 연결을 되살릴 방법이 없고,
        # 억지로 되살리면 제약이 다시 붙을 때 또 막힙니다.
        migrations.RunPython(unlink_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='pendingquestion',
            constraint=models.UniqueConstraint(
                condition=models.Q(('run', None), _negated=True),
                fields=('run',), name='uq_pending_question_run'),
        ),
    ]
