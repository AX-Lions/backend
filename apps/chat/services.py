"""
방을 만들고 찾는 규칙.

단체방(TEAM · PROJECT)은 **없으면 그때 만듭니다.** 팀·프로젝트를 만드는 쪽에서
같이 만들면 좋겠지만, 그러면 이미 만들어진 팀들에는 방이 영영 안 생깁니다.
조회 시점에 채우면 기존 데이터도 자동으로 메워집니다.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.orgs.models import Project, ProjectMember, Team, TeamMember

from .models import ChatRoom, RoomMember, RoomType


def direct_key(user_a_id, user_b_id):
    """1:1 방 지문. 누가 먼저 걸든 같은 값이 나와야 방이 두 개 안 생깁니다."""
    return ",".join(sorted([str(user_a_id), str(user_b_id)]))


def peer_agent_key(requester_id, owner_id):
    """`요청자 → 상대의 대리인` 방. 방향이 있어 정렬하지 않습니다."""
    return f"{requester_id}>agent:{owner_id}"


def _sync_members(room, user_ids):
    """방 참여자를 원본(팀·프로젝트 멤버)에 맞춰 채웁니다. 빼지는 않습니다."""
    have = set(RoomMember.objects.filter(room=room).values_list("user_id", flat=True))
    missing = [u for u in user_ids if u not in have]
    if missing:
        RoomMember.objects.bulk_create(
            [RoomMember(room=room, user_id=u) for u in missing], ignore_conflicts=True)
    return room


def ensure_team_room(team):
    room = ChatRoom.all_objects.filter(type=RoomType.TEAM, team=team).first()
    if room is None:
        try:
            with transaction.atomic():
                room = ChatRoom.objects.create(
                    type=RoomType.TEAM, team=team, team_name=team.name,
                    title=team.name)
        except IntegrityError:                 # 동시에 두 번 들어온 경우
            room = ChatRoom.all_objects.get(type=RoomType.TEAM, team=team)
    member_ids = list(TeamMember.objects.filter(team=team).values_list("user_id", flat=True))
    return _sync_members(room, member_ids)


def ensure_project_room(project):
    room = ChatRoom.all_objects.filter(type=RoomType.PROJECT, project=project).first()
    if room is None:
        try:
            with transaction.atomic():
                room = ChatRoom.objects.create(
                    type=RoomType.PROJECT, project=project, project_name=project.name,
                    team_id=project.team_id, team_name=project.team_name,
                    title=project.name)
        except IntegrityError:
            room = ChatRoom.all_objects.get(type=RoomType.PROJECT, project=project)

    # 프로젝트 이름이 바뀌면 사이드바 표기도 따라가야 합니다.
    if room.project_name != project.name or room.team_name != project.team_name:
        room.project_name, room.team_name = project.name, project.team_name
        room.save(update_fields=["project_name", "team_name", "updated_at"])

    if not project.group_chat_room_id or project.group_chat_room_id != room.id:
        Project.objects.filter(pk=project.pk).update(group_chat_room_id=room.id)
        project.group_chat_room_id = room.id

    member_ids = list(ProjectMember.objects.filter(project=project)
                      .values_list("user_id", flat=True))
    return _sync_members(room, member_ids)


def ensure_ai_room(user):
    """`나의 AI 대리인` 방. 사용자당 하나, 팀과 무관합니다."""
    key = f"ai:{user.id}"
    room = ChatRoom.all_objects.filter(type=RoomType.AI, dedupe_key=key).first()
    if room is None:
        try:
            with transaction.atomic():
                room = ChatRoom.objects.create(
                    type=RoomType.AI, dedupe_key=key, title="나의 AI 대리인",
                    agent_owner=user, created_by=user)
        except IntegrityError:
            room = ChatRoom.all_objects.get(type=RoomType.AI, dedupe_key=key)
    RoomMember.objects.get_or_create(room=room, user=user)
    return room


def sync_all_group_rooms(user):
    """
    사이드바를 그리기 직전에 부릅니다.

    이 사용자가 속한 팀·프로젝트의 단체방을 한 번에 메웁니다. 방이 없어서
    사이드바에 팀이 통째로 안 보이는 상황을 막습니다.
    """
    teams = list(Team.objects.filter(members__user=user).distinct())
    for team in teams:
        ensure_team_room(team)
    projects = list(Project.objects.filter(members__user=user).distinct())
    for project in projects:
        ensure_project_room(project)
    ensure_ai_room(user)


def touch(room, when=None):
    """마지막 메시지 시각 갱신. 사이드바 정렬 기준입니다."""
    room.last_message_at = when or timezone.now()
    room.save(update_fields=["last_message_at", "updated_at"])
