"""Tests for async governance pipeline."""

import asyncio
import pytest
from agentmesh import AgentMesh
from agentmesh.policy.engine import Policy


@pytest.fixture
def async_mesh():
    policy = Policy.from_dict({
        "name": "async-test-policy",
        "budget": {"per_run_tokens": 10_000, "hard_stop": True},
        "circuit_breaker": {"max_iterations": 5},
    })
    return AgentMesh(policy=policy)


class MockAsyncResponse:
    class usage:
        input_tokens = 100
        output_tokens = 50
    model = "claude-haiku-4-5"


async def mock_async_llm(**kwargs):
    await asyncio.sleep(0)  # simulate async I/O
    return MockAsyncResponse()


def sync_llm(**kwargs):
    return MockAsyncResponse()


@pytest.mark.asyncio
async def test_intercept_async_basic(async_mesh):
    result = await async_mesh.intercept_async(mock_async_llm, model="claude-haiku-4-5", messages=[])
    assert result is not None
    assert async_mesh.budget.tokens_used == 150


@pytest.mark.asyncio
async def test_intercept_async_increments_circuit_breaker(async_mesh):
    await async_mesh.intercept_async(mock_async_llm, model="claude-haiku-4-5", messages=[])
    assert async_mesh.circuit_breaker.iteration_count == 1


@pytest.mark.asyncio
async def test_intercept_async_with_sync_function(async_mesh):
    result = await async_mesh.intercept_async(sync_llm, model="claude-haiku-4-5", messages=[])
    assert result is not None


@pytest.mark.asyncio
async def test_intercept_async_concurrent(async_mesh):
    async_mesh2 = AgentMesh(policy=Policy.from_dict({
        "name": "async-test-2",
        "budget": {"per_run_tokens": 50_000},
    }))
    results = await asyncio.gather(
        async_mesh2.intercept_async(mock_async_llm, model="claude-haiku-4-5", messages=[]),
        async_mesh2.intercept_async(mock_async_llm, model="claude-haiku-4-5", messages=[]),
        async_mesh2.intercept_async(mock_async_llm, model="claude-haiku-4-5", messages=[]),
    )
    assert len(results) == 3
    assert async_mesh2.budget.tokens_used == 450
