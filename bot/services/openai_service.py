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