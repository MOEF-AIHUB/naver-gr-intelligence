"""Smoke tests to verify package imports."""


def test_package_imports() -> None:
    import legi_ai

    assert legi_ai.__version__


def test_config_loads() -> None:
    from legi_ai.config import settings

    assert settings.claude_model_primary == "claude-opus-4-7"
    assert settings.embedding_dim == 1024


def test_submodules_importable() -> None:
    import legi_ai.agents  # noqa: F401
    import legi_ai.api  # noqa: F401
    import legi_ai.evaluation  # noqa: F401
    import legi_ai.ingestion  # noqa: F401
    import legi_ai.mcp  # noqa: F401
    import legi_ai.rag  # noqa: F401
