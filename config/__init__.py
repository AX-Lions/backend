"""
Django 가 뜰 때 Celery 앱을 함께 올립니다.

이걸 빼면 @shared_task 가 어느 앱에 속하는지 몰라, 워커에서는 보이는데
개발 서버에서는 태스크를 못 찾는 상황이 생깁니다.
"""
from .celery import app as celery_app

__all__ = ("celery_app",)
