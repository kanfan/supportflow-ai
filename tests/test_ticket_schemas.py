from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.tickets.schemas import TicketCreateRequest


@pytest.mark.parametrize("author_type", ["customer", "system"])
def test_client_cannot_claim_customer_or_system_identity(author_type: str) -> None:
    with pytest.raises(ValidationError, match="Input should be 'agent'"):
        TicketCreateRequest.model_validate(
            {
                "subject": "Cannot sign in",
                "customer_id": str(uuid4()),
                "initial_message": {
                    "author_type": author_type,
                    "body": "The sign-in page rejects my password.",
                },
            }
        )


def test_client_cannot_supply_message_author_identifiers() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TicketCreateRequest.model_validate(
            {
                "subject": "Cannot sign in",
                "customer_id": str(uuid4()),
                "initial_message": {
                    "author_type": "agent",
                    "author_user_id": str(uuid4()),
                    "body": "Customer reported a sign-in problem.",
                },
            }
        )
