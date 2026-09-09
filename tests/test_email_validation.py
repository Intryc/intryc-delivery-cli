import json

import pytest

from intryc_delivery.contract.semantic import ContractValidationError, parse_ticket
from intryc_delivery.validation import schema_validators


@pytest.mark.parametrize(
    "email,accepted",
    [
        ("agent@example.com", True),
        (" Agent+support@Sub.Example.com ", True),
        ("agent@localhost", False),
        ("agent@@example.com", False),
        ("agent name@example.com", False),
    ],
)
def test_email_schema_matches_validation(root, email, accepted):
    ticket = json.loads((root / "ticket_details/written.json").read_bytes())
    ticket["users"][0]["email"] = email
    assert schema_validators()["ticket-1.0.schema.json"].is_valid(ticket) is accepted
    if accepted:
        validated = parse_ticket(ticket, expected_source_ticket_id=ticket["source_ticket_id"])
        assert validated.data["users"][0]["email"] == email.strip().lower()
    else:
        with pytest.raises(ContractValidationError) as error:
            parse_ticket(ticket, expected_source_ticket_id=ticket["source_ticket_id"])
        assert "INVALID_USER_EMAIL" in {issue.code for issue in error.value.issues}
