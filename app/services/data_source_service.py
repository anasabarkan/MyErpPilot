"""
Service layer for DataSource operations.

Handles building connection strings, testing connections, and fetching
database schemas for AI use.
"""

from typing import Dict, List, Any
from uuid import UUID
from datetime import datetime, timezone
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.data_source import DataSource
from app.repositories.data_source_repository import get_data_source_by_id, save_schema_cache

logger = logging.getLogger(__name__)


def build_connection_url(data_source: DataSource) -> str:
    """
    Construct the database connection URL from DataSource properties.
    Supports postgresql for now.
    """
    # E.g., postgresql://user:password@host:port/database
    url = f"{data_source.db_type}://{data_source.username}:{data_source.password}@{data_source.host}:{data_source.port}/{data_source.database}"
    return url


def test_connection(db: Session, data_source: DataSource) -> Dict[str, Any]:
    """
    Attempt to connect to the external database.
    Returns a dictionary with 'success' and 'message'.
    """
    url = build_connection_url(data_source)
    try:
        # Create a temporary engine and attempt a simple connection
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        with engine.connect() as connection:
            # Just test if we can execute a simple query
            connection.execute(text("SELECT 1"))
        
        # Update last_tested_at
        data_source.last_tested_at = datetime.now(timezone.utc)
        db.add(data_source)
        db.commit()
        
        return {"success": True, "message": "Connection successful"}
    except SQLAlchemyError as e:
        logger.error(f"Failed to connect to data source {data_source.id}: {str(e)}")
        return {"success": False, "message": f"Connection failed: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected error testing data source {data_source.id}: {str(e)}")
        return {"success": False, "message": "An unexpected error occurred during connection testing"}


def get_schema(data_source: DataSource) -> Dict[str, Any]:
    """
    Connect to the data source and extract the schema (tables and columns).
    Only querying the 'public' schema for PostgreSQL.
    """
    url = build_connection_url(data_source)
    
    schema_dict: Dict[str, List[str]] = {}
    
    try:
        engine = create_engine(url)
        with engine.connect() as connection:
            # 1. Get all tables in the 'public' schema
            tables_query = text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
            tables_result = connection.execute(tables_query).fetchall()
            
            for row in tables_result:
                table_name = row[0]
                schema_dict[table_name] = []
            
            # 2. Get all columns for these tables
            columns_query = text(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'"
            )
            columns_result = connection.execute(columns_query).fetchall()
            
            for row in columns_result:
                table_name = row[0]
                column_name = row[1]
                if table_name in schema_dict:
                    schema_dict[table_name].append(column_name)
                    
        return {"tables": schema_dict}
    except Exception as e:
        logger.error(f"Failed to extract schema for data source {data_source.id}: {str(e)}")
        raise e


def cache_schema(db: Session, data_source_id: UUID) -> Dict[str, Any]:
    """
    Fetch the schema and store it in the DataSource's schema_cache field.
    """
    data_source = get_data_source_by_id(db, data_source_id)
    if not data_source:
        raise ValueError(f"DataSource with ID {data_source_id} not found")
        
    schema_data = get_schema(data_source)
    
    # Save to the repository
    save_schema_cache(db, data_source_id, schema_data)
    
    return schema_data
