class RenderPlanError(ValueError):
    """Base error for invalid renderplan contract values."""


class ValidationError(RenderPlanError):
    """A model violates the renderplan contract."""


class CodecError(RenderPlanError):
    """A persisted TOML document cannot be decoded or encoded."""
