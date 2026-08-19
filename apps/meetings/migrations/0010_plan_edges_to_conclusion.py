"""
회의 모드의 `계획`(PLAN) 화살표를 `결론`(CONCLUSION) 으로 옮깁니다.

필터가 여섯 칸인데 `계획` 이 없어, 태스크 후보가 하나라도 쌓이면 화면에
없는 칸이 필터 목록에 새로 떴습니다 (이슈 #67).

되돌리기는 두지 않습니다. 옮긴 뒤에는 원래 `결론` 이던 행과 구별할 수 없어,
되돌리면 남의 행까지 `계획` 으로 만듭니다.
"""
from django.db import migrations


def to_conclusion(apps, schema_editor):
    FlowEdge = apps.get_model("meetings", "FlowEdge")
    FlowEdge.objects.filter(category="MEETING", content_type="PLAN").update(
        content_type="CONCLUSION")


class Migration(migrations.Migration):

    dependencies = [("meetings", "0009_utterance_is_agent")]

    operations = [migrations.RunPython(to_conclusion, migrations.RunPython.noop)]
