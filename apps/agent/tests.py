"""
대리인 단위 테스트.

여기서 보는 것은 커버리지가 아니라 **막아야 하는 경로가 실제로 막히는가** 입니다.
스모크 스크립트와 같은 기준입니다.
"""
from django.test import SimpleTestCase

from apps.agent.services.skills import (SkillBase, SkillContext, SkillKind,
                                        SkillRegistry, SkillResult)


class _Echo(SkillBase):
    name = "echo"
    description = "받은 값을 그대로 돌려줍니다."
    parameters = {"type": "object", "properties": {"v": {"type": "string"}}}

    def run(self, args, ctx):
        return SkillResult(ok=True, message="ok", data={"v": args.get("v")})


class _Boom(SkillBase):
    name = "boom"
    description = "터집니다."

    def run(self, args, ctx):
        raise RuntimeError("의도된 폭발")


class _Wrong(SkillBase):
    name = "wrong"
    description = "SkillResult 가 아닌 것을 돌려줍니다."

    def run(self, args, ctx):
        return {"ok": True}


class _Writer(SkillBase):
    name = "writer"
    description = "쓰기 스킬."
    kind = SkillKind.WRITE

    def run(self, args, ctx):
        return SkillResult(ok=True, message="wrote")


def _ctx():
    return SkillContext(principal_id="u-1", actor_id="u-2")


class RegistryTest(SimpleTestCase):

    def setUp(self):
        self.reg = SkillRegistry()
        self.reg.register(_Echo())

    # ── 정상 경로 한 번 ────────────────────────────────────
    def test_dispatch(self):
        r = self.reg.dispatch("echo", {"v": "hi"}, _ctx())
        self.assertTrue(r.ok)
        self.assertEqual(r.data["v"], "hi")

    # ── 막아야 하는 것들 ───────────────────────────────────
    def test_unknown_skill_is_not_an_exception(self):
        """LLM 이 없는 이름을 지어내도 루프가 죽으면 안 됩니다."""
        r = self.reg.dispatch("nope", {}, _ctx())
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "not_found")

    def test_exception_is_captured(self):
        """루프 한가운데서 터지면 steps·evidence 가 통째로 날아갑니다."""
        self.reg.register(_Boom())
        r = self.reg.dispatch("boom", {}, _ctx())
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "internal")
        self.assertIn("의도된 폭발", r.message)

    def test_wrong_return_type_is_rejected(self):
        """dict 를 돌려주면 루프가 조용히 오작동합니다."""
        self.reg.register(_Wrong())
        r = self.reg.dispatch("wrong", {}, _ctx())
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, "internal")

    def test_duplicate_name_is_rejected(self):
        """나중 것이 조용히 이기면 '고쳤는데 안 바뀐다' 가 됩니다."""
        with self.assertRaises(ValueError):
            self.reg.register(_Echo())

    def test_nameless_skill_is_rejected(self):
        class _NoName(SkillBase):
            def run(self, args, ctx):
                return SkillResult(ok=True, message="")

        with self.assertRaises(ValueError):
            self.reg.register(_NoName())

    # ── 카탈로그 ───────────────────────────────────────────
    def test_catalog_shape(self):
        spec = self.reg.build_catalog()[0]
        self.assertEqual(set(spec), {"name", "description", "parameters"})

    def test_catalog_filters_by_kind(self):
        """읽기 전용 구간에서는 쓰기 스킬을 아예 안 보여줍니다."""
        self.reg.register(_Writer())
        names = [s["name"] for s in self.reg.build_catalog(kind=SkillKind.READ)]
        self.assertEqual(names, ["echo"])


class ContextTest(SimpleTestCase):

    def test_is_principal(self):
        """유보 규칙 R2 가 이 판정 위에 섭니다."""
        ctx = _ctx()
        self.assertTrue(ctx.is_principal("u-1"))
        self.assertFalse(ctx.is_principal("u-2"))

    def test_is_principal_accepts_uuid_like(self):
        """DB 에서 온 UUID 객체와 문자열이 섞여도 같게 봐야 합니다."""
        import uuid
        uid = uuid.uuid4()
        ctx = SkillContext(principal_id=str(uid))
        self.assertTrue(ctx.is_principal(uid))
