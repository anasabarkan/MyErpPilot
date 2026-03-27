"""
Pydantic schemas for DataSource.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DataSourceBase(BaseModel):
    name: str = Field(..., example="Production DB")
    db_type: str = Field(default="postgresql", example="postgresql")
    host: str = Field(..., example="localhost")
    port: str = Field(..., example="5432")
    database: str = Field(..., example="my_erp")
    username: str = Field(..., example="postgres")
    is_active: bool = Field(default=True)


class DataSourceCreate(DataSourceBase):
    password: str = Field(..., example="secret")


class DataSourceUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[str] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None


class DataSourceResponse(DataSourceBase):
    id: UUID
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    last_tested_at: Optional[datetime] = None
    # Provide a masked placeholder instead of exposing the password
    password_masked: str = Field(default="********")
    # Determine if schema_cache is populated (don't send huge schemas automatically)
    has_schema_cache: bool = Field(default=False)

    class Config:
        orm_mode = True
        from_attributes = True


class DataSourceTestResponse(BaseModel):
    success: bool
    message: str = Field(..., example="Connection successful")


class DataSourceSchemaResponse(BaseModel):
    tables: Dict[str, List[str]] = Field(
        ...,
        example={"users": ["id", "email"], "orders": ["id", "total"]}
    )
