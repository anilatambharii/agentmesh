# Contributing to AgentMesh

Thank you for your interest in contributing to AgentMesh. This project is Apache 2.0 licensed and community-driven.

## Ways to Contribute

- **Bug reports** — Open an issue with reproduction steps
- **Feature requests** — Open an issue with use case and proposed API
- **Code contributions** — Fork, branch, PR (see below)
- **Documentation** — Improve guides, add examples, fix typos
- **Integration plugins** — Add support for a new agent framework

## Development Setup

```bash
git clone https://github.com/anilatambharii/agentmesh
cd agentmesh
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=agentmesh --cov-report=term-missing

# Specific module
pytest tests/test_core.py -v
```

## Code Style

We use:
- **black** for formatting (`black agentmesh/ tests/`)
- **ruff** for linting (`ruff check agentmesh/ tests/`)
- **mypy** for type checking (`mypy agentmesh/`)

Pre-commit hooks enforce these automatically.

## Pull Request Process

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Write tests for new functionality (tests live in `tests/`)
4. Ensure all tests pass: `pytest tests/ -v`
5. Run linting: `ruff check . && black --check .`
6. Open a PR with a clear description of what and why

## Adding a New Integration

New framework integrations live in `agentmesh/integrations/`. Each integration:

1. Exports a `wrap_*()` function that accepts `(agent_or_object, mesh)` and returns a governance proxy
2. Intercepts LLM calls by calling `mesh.circuit_breaker.check()`, `mesh.budget.check_pre_call()`, `mesh.audit.record_call()`, and `mesh.budget.record_usage()` at minimum
3. Forwards `__getattr__` to the wrapped object to preserve the original API
4. Includes a module docstring with a usage example

See `agentmesh/integrations/autogen.py` as a reference implementation.

## Adding a Policy Template

Templates live in `agentmesh/templates/`. Each template is a YAML file that:

1. Follows the `PolicySchema` Pydantic model structure
2. Includes a comment block explaining the target industry/company and compliance requirements
3. Has `metadata.template` set to the filename stem
4. Is tested in `tests/test_templates.py`

## Reporting Security Issues

Please see [SECURITY.md](SECURITY.md) for responsible disclosure guidelines.  
Do **not** open public GitHub issues for security vulnerabilities.

## License

By contributing to AgentMesh, you agree that your contributions will be licensed under the Apache 2.0 License.
