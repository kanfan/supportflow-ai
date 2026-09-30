from collections.abc import Sequence

from app.classification.errors import ClassificationError, ProviderUnavailableError
from app.classification.inputs import OpeningMessage, prepare_input
from app.classification.providers import ClassificationProvider
from app.classification.schemas import ClassificationResult, parse_provider_output


def classify_opening_request(
    subject: str,
    messages: Sequence[OpeningMessage],
    provider: ClassificationProvider,
) -> ClassificationResult:
    prepared = prepare_input(subject, messages)
    if prepared.insufficient_context:
        return ClassificationResult(outcome="insufficient_context", category=None)
    # A1a performs one call. Real-provider retry/deadline policy is an A1b gate.
    try:
        raw = provider.classify(prepared.payload)
    except ClassificationError:
        raise
    except Exception:
        raise ProviderUnavailableError from None
    return parse_provider_output(raw)
