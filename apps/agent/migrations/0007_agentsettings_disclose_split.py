"""
공개 설정을 작업 · 계획 · 생각 셋으로 쪼갭니다.

옛 값을 셋에 그대로 복사한 뒤 옛 칸을 지웁니다. 기본값(True)으로 새로 만들면
공개를 꺼 뒀던 사람의 기록이 마이그레이션 한 번으로 열립니다.
"""
from django.db import migrations, models


def split(apps, schema_editor):
    AgentSettings = apps.get_model("agent", "AgentSettings")
    AgentSettings.objects.filter(disclose_work=False, disclose_plan=False, disclose_thought=False).update(
        disclose_work=False, disclose_plan=False, disclose_thought=False)


def merge_back(apps, schema_editor):
    AgentSettings = apps.get_model("agent", "AgentSettings")
    for row in AgentSettings.objects.all():
        row.disclose_work_plan_thought = (
            row.disclose_work or row.disclose_plan or row.disclose_thought)
        row.save(update_fields=["disclose_work_plan_thought"])


class Migration(migrations.Migration):

    dependencies = [("agent", "0006_agentsettings_agent_name")]

    operations = [
        migrations.AddField(
            model_name="agentsettings",
            name="disclose_work",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="agentsettings",
            name="disclose_plan",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="agentsettings",
            name="disclose_thought",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(split, merge_back),
        migrations.RemoveField(
            model_name="agentsettings",
            name="disclose_work_plan_thought",
        ),
    ]
