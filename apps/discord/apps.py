from django.apps import AppConfig


class DiscordConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.discord"
    label = "discord_bot"          # `discord` 는 봇 라이브러리 이름과 겹칩니다
