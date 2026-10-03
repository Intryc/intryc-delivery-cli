import json

import pytest

from intryc_delivery.contract.semantic import ContractValidationError, parse_ticket
from intryc_delivery.validation import schema_validators


@pytest.mark.parametrize(
    "field,limit,code",
    [
        ("key", 512, "INVALID_CUSTOM_FIELDS"),
        ("user_key", 512, "INVALID_USER_CUSTOM_FIELDS"),
        ("email", 254, "INVALID_USER_EMAIL"),
        ("field_name", 512, "FIELD_NAME_REQUIRED"),
        ("value", 512, "INVALID_FIELD_EVENT_VALUE"),
        ("previous_value", 512, "INVALID_FIELD_EVENT_VALUE"),
    ],
)
@pytest.mark.parametrize("extra", [0, 1])
def test_string_limits_match_schema(root, field, limit, code, extra):
    ticket = json.loads((root / "ticket_details/written.json").read_bytes())
    ticket["field_events"] = [
        {
            "source_event_id": "event-1",
            "created_at": ticket["created_at"],
            "type": "change",
            "field_name": "status",
        }
    ]
    value = "🙂" * (limit + extra)
    if field == "key":
        ticket["custom_fields"] = {value: "sample"}
    elif field == "user_key":
        ticket["users"][0]["custom_fields"] = {value: "sample"}
    elif field == "email":
        ticket["users"][0]["email"] = "🙂" * (limit + extra - len("@example.com")) + "@example.com"
    else:
        ticket["field_events"][0]["field_name"] = "status"
        ticket["field_events"][0][field] = value
    assert schema_validators()["ticket-1.0.schema.json"].is_valid(ticket) is (extra == 0)
    if extra == 0:
        parse_ticket(ticket, expected_source_ticket_id=ticket["source_ticket_id"])
    else:
        with pytest.raises(ContractValidationError) as error:
            parse_ticket(ticket, expected_source_ticket_id=ticket["source_ticket_id"])
        assert code in {issue.code for issue in error.value.issues}


@pytest.mark.parametrize(
    "field_name,value",
    [("status", None), ("status", True), ("status", 42), ("description", "x" * 4096)],
)
def test_status_bound_keeps_other_scalar_values_supported(root, field_name, value):
    ticket = json.loads((root / "ticket_details/written.json").read_bytes())
    ticket["field_events"] = [
        {
            "source_event_id": "event-1",
            "created_at": ticket["created_at"],
            "type": "change",
            "field_name": "status",
        }
    ]
    ticket["field_events"][0].update(field_name=field_name, value=value, previous_value=value)
    assert schema_validators()["ticket-1.0.schema.json"].is_valid(ticket)
    parse_ticket(ticket, expected_source_ticket_id=ticket["source_ticket_id"])
