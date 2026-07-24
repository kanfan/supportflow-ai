from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.tickets.schemas import TicketCreateRequest


def test_customer_message_requires_a_customer() -> None:
    with pytest.raises(ValidationError, match="customer_id is required"):
        TicketCreateRequest.model_validate(
            {
                "subject": "Cannot sign in",
                "initial_message": {
                    "author_type": "customer",
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
