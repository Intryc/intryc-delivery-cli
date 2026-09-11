import json

import pytest

from intryc_delivery.contract.semantic import ContractValidationError, parse_ticket
from intryc_delivery.validation import schema_validators


@pytest.mark.parametrize("length,accepted", [(255, True), (256, False), (4096, False)])
@pytest.mark.parametrize("character", ["a", "😀"])
@pytest.mark.parametrize("field", ["tags", "value", "previous_value"])
def test_tag_length_matches_schema(root, length, accepted, character, field):
    ticket = json.loads((root / "ticket_details/written.json").read_bytes())
    if field == "tags":
        ticket["tags"] = [character * length]
    else:
        ticket["tag_events"] = [
            {
                "source_event_id": "event-1",
                "created_at": ticket["created_at"],
                "type": "add",
                field: [character * length],
            }
        ]
    assert schema_validators()["ticket-1.0.schema.json"].is_valid(ticket) is accepted
    if accepted:
        parse_ticket(ticket, expected_source_ticket_id=ticket["source_ticket_id"])
    else:
        with pytest.raises(ContractValidationError) as error:
            parse_ticket(ticket, expected_source_ticket_id=ticket["source_ticket_id"])
        assert ("INVALID_TAGS" if field == "tags" else "INVALID_TAG_EVENT_VALUE") in {
            issue.code for issue in error.value.issues
        }
