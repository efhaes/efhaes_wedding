from pydantic import BaseModel


class RsvpStatistics(BaseModel):
    total_guests: int
    responded_guests: int
    not_responded_guests: int
    attending_guests: int
    not_attending_guests: int
    total_attending_people: int
