from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import Ctx, ctx
from ..models import AiInvestigation
from ..services import agent, llm
from ..util import val

router = APIRouter(prefix="/api/agent")


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@router.post("/ask")
def ask(b: Ask, c: Ctx = Depends(ctx("agent.use"))):
    out = agent.ask(c, b.question)
    c.db.commit()
    return out


@router.get("/status")
def status(c: Ctx = Depends(ctx("agent.use"))):
    return {"llm_enabled": llm.enabled(), "mode": "llm" if llm.enabled() else "deterministic",
            "tools": sorted(agent.TOOLS), "note": None if llm.enabled() else "OPENAI_API_KEY not set: the deterministic investigator answers using the same audited tools."}


@router.get("/history")
def history(c: Ctx = Depends(ctx("agent.use"))):
    rows = c.db.query(AiInvestigation).filter_by(org_id=c.org.id).order_by(AiInvestigation.created_at.desc()).limit(20)
    return [{"question": r.question, "answer": r.answer, "tool_calls": r.tool_calls, "at": val(r.created_at)} for r in rows]
