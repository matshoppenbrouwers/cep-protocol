"""Reference harness adapters.

Only the Hermes adapter (SSE/HTTP over an OpenAI-compatible API) ships here.
It is imported lazily so the stdlib-only core stays importable without the
``hermes`` extra installed.
"""

__all__ = ["HermesAdapter"]


def __getattr__(name: str):
    if name == "HermesAdapter":
        from cep.adapters.hermes import HermesAdapter

        return HermesAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
