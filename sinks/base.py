from typing import Protocol
from pydantic import BaseModel


class Sink(Protocol):
    def save(self, record: BaseModel, ctx) -> None: ...
