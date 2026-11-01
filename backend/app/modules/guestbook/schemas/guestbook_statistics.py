from pydantic import BaseModel


class GuestbookStatistics(BaseModel):
    total_messages: int
    pending_messages: int
    approved_messages: int
    hidden_messages: int
