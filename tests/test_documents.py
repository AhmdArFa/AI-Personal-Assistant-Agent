from personal_assistant_agent.orchestrator import (
    AgentOrchestrator,
    Plan,
    PlanStep,
    RunStatus,
    StaticPlanner,
    TenantContext,
    registry_with_documents,
    seeded_corpus,
)

CTX = TenantContext(user_id="user-1", workspace_id="ws-1")
OTHER = TenantContext(user_id="user-9", workspace_id="ws-2")


def _search(query, context=CTX, top_k=5):
    plan = Plan(
        goal="search",
        steps=[
            PlanStep(
                id="s1",
                description="search",
                tool_name="search_documents",
                tool_args={"query": query, "top_k": top_k},
            )
        ],
    )
    agent = AgentOrchestrator(
        registry=registry_with_documents(seeded_corpus()), planner=StaticPlanner(plan)
    )
    return agent.run("search", context=context)


def _results(result):
    return result.state.observations[-1].result


def test_english_query_finds_own_document():
    result = _search("quarterly revenue")
    assert result.status is RunStatus.COMPLETED
    items = _results(result)
    assert len(items) == 1
    assert items[0]["document_id"] == "doc-en-report"
    assert items[0]["source_location"] == "reports/q3.md, page 1"
    assert items[0]["trust_label"] == "untrusted_data"
    assert "quarterly" in items[0]["excerpt"].lower()


def test_arabic_query_finds_arabic_document():
    result = _search("خطة المشروع")
    assert result.status is RunStatus.COMPLETED
    items = _results(result)
    assert len(items) == 1
    assert items[0]["document_id"] == "doc-ar-plan"
    assert "المشروع" in items[0]["excerpt"]


def test_cross_workspace_documents_never_returned():
    # ws-2 owns a document full of the same keywords; ws-1 must not see it.
    items = _results(_search("quarterly report revenue"))
    ids = [item["document_id"] for item in items]
    assert "doc-other-workspace" not in ids
    # And ws-2 searching its own corpus finds only its own document.
    items = _results(_search("quarterly report", context=OTHER))
    ids = [item["document_id"] for item in items]
    assert ids == ["doc-other-workspace"]


def test_top_k_is_bounded():
    items = _results(_search("report notes quarterly", top_k=1))
    assert len(items) == 1


def test_too_short_query_fails_validation_without_searching():
    result = _search("a")
    assert result.status is RunStatus.FAILED
    error = result.state.observations[-1].error or ""
    assert "at least 2 characters" in error
