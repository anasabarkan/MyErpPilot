"""
Pydantic schemas for Odoo data sources.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OdooModel(BaseModel):
    model: str
    name: str


class DataSourceBase(BaseModel):
    name: str = Field(..., examples=["Odoo Production"])
    odoo_url: str = Field(..., examples=["http://localhost:8069"])
    database_name: str = Field(..., examples=["odoo"])
    username: str = Field(..., examples=["admin"])


class DataSourceCreate(DataSourceBase):
    api_key: str = Field(..., min_length=1)


class DataSourceUpdate(BaseModel):
    name: Optional[str] = None
    odoo_url: Optional[str] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    api_key: Optional[str] = Field(default=None, min_length=1)


class DataSourceConnectionTestRequest(BaseModel):
    odoo_url: str
    database_name: str
    username: str
    api_key: str = Field(..., min_length=1)


class DataSourceResponse(DataSourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_by: UUID
    is_active: bool
    api_key_configured: bool
    models_count: int = 0
    last_tested_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class DataSourceModelsResponse(BaseModel):
    models: list[OdooModel]


class DataSourceTestResponse(BaseModel):
    success: bool
    message: str
    models: list[OdooModel] = Field(default_factory=list)


class ActiveDataSourceResponse(BaseModel):
    active_data_source: Optional[DataSourceResponse] = None
