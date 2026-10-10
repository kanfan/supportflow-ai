from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Protocol

from app.classification.errors import ClassificationError
from app.classification.inputs import ProviderInput


class ClassificationProvider(Protocol):
    def classify(self, payload: ProviderInput) -> str:
        """Return raw schema JSON or raise a safe typed provider error."""
        ...


class FakeClassificationProvider:
    """Scripted fixture response, not a classifier or model-quality baseline.

    Bind the fixture when constructing the fake so fixture IDs never enter the
    production provider payload. Calls track invocation count, never ticket text.
    """

    mode = "fake"

    def __init__(
        self,
        fixture_id: str,
        scripts: Mapping[str, str | type[ClassificationError]],
    ) -> None:
        if fixture_id not in scripts:
            raise ValueError("Unknown fake classification fixture")
        self._response = scripts[fixture_id]
        self.calls = 0

    def classify(self, payload: ProviderInput) -> str:
        self.calls += 1
        if isinstance(self._response, str):
            return self._response
        raise self._response()

    @property
    def script_fingerprint(self) -> str:
        """Internal config identity; never expose script text in application rows."""
        # Domain separation is essential: a raw response equal to an error's
        # category must not reuse that error script's application claim.
        if isinstance(self._response, str):
            script = {"kind": "response", "value": self._response}
        else:
            script = {
                "kind": "error",
                "type": f"{self._response.__module__}.{self._response.__qualname__}",
                "category": self._response.category,
            }
        canonical = json.dumps(
            {"version": "fake_script.v2", "script": script},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()
