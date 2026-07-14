from pydantic import BaseModel


class JobPositionOut(BaseModel):
    id: int
    nome: str
    slug: str
