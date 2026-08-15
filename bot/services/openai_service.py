import json

from openai import AsyncOpenAI


class OpenAIService:
    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key) if api_key else None
        self.model = model

    async def llm_chat_reply(self, user_message: str, author_name: str) -> str:
        """일반 채팅: 사용자 메시지에 LLM이 직접 답한다."""
        if self.client is None:
            return "OPENAI_API_KEY가 설정되지 않아 채팅 기능을 쓸 수 없습니다."

        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "너는 Discord 서버의 AI 도우미다. 간결하고 친절하게 한국어로 답한다."},
                {"role": "user", "content": f"{author_name}: {user_message}"},
            ],
            max_tokens=500,
        )

        return resp.choices[0].message.content.strip()

    async def generate_meeting_wrapup(self, transcript: list[dict]) -> dict:
        """회의 종료 시 최근 대화를 요약하고 TODO 리스트로 정리한다."""
        if self.client is None:
            return {"summary": "OPENAI_API_KEY가 설정되지 않아 요약을 생성할 수 없습니다.", "todos": []}

        if not transcript:
            return {"summary": "회의 중 기록된 대화가 없습니다.", "todos": []}

        convo = "\n".join(f"[{t['author']}] {t['content']}" for t in transcript)

        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "너는 회의 대화록을 분석하는 비서다. 아래 JSON 스키마로만 응답한다: "
                    '{"summary": "3~5문장 요약", "todos": ["할 일1", "할 일2"]}'
                )},
                {"role": "user", "content": convo[:12000]},
            ],
            response_format={"type": "json_object"},
            max_tokens=800,
        )

        try:
            data = json.loads(resp.choices[0].message.content)
        except (ValueError, TypeError):
            data = {"summary": resp.choices[0].message.content, "todos": []}

        return {"summary": data.get("summary", ""), "todos": data.get("todos", [])}