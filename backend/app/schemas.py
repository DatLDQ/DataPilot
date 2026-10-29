from pydantic import BaseModel, Field


class SampleRequest(BaseModel):
    name: str = "ecommerce_sales"


class CreateConversation(BaseModel):
    dataset_id: str


class RenameConversation(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class DatasetSummary(BaseModel):
    id: str
    name: str
    filename: str
    created_at: str
    tables: list[dict]


def summarize_dataset(ds: dict) -> DatasetSummary:
    return DatasetSummary(
        id=ds["id"],
        name=ds["name"],
        filename=ds["filename"],
        created_at=ds["created_at"],
        tables=[
            {"name": t["name"], "row_count": t["row_count"], "column_count": len(t["columns"])}
            for t in ds["profile"]["tables"]
        ],
    )
