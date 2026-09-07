from enum import IntEnum

from pydantic import BaseModel


class ExitCode(IntEnum):
    SUCCESS = 0
    VALIDATION = 1
    CONFIGURATION = 2
    TRANSPORT = 3


class Issue(BaseModel):
    code: str
    message: str
    path: str | None = None
    source_ticket_id: str | None = None
    json_pointer: str | None = None
    corrective_action: str = "Correct the delivery and retry."

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.source_ticket_id or "", self.path or "", self.json_pointer or "", self.code)


class DeliveryError(Exception):
    def __init__(self, issues: list[Issue], exit_code: ExitCode = ExitCode.VALIDATION):
        self.issues = sorted(issues, key=Issue.sort_key)
        self.exit_code = exit_code
        super().__init__("Delivery operation failed")


def fail(
    code: str, message: str, *, exit_code: ExitCode = ExitCode.VALIDATION, path: str | None = None
) -> None:
    raise DeliveryError([Issue(code=code, message=message, path=path)], exit_code)
