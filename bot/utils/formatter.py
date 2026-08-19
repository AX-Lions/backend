import discord

def build_agent_embed(agent_name: str, answer: str, evidence_count: int, detail_url: str,
                      confidence: str | None = None, withheld: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=f"🤖 AI 대리인 · {agent_name}",
        description="사용자 확인 필요" if withheld else answer,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="근거", value=f"{evidence_count}건 · [상세 보기]({detail_url})", inline=False)

    if confidence:
        embed.set_footer(text=f"신뢰도: {confidence}")

    return embed