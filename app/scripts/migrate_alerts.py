from app.database.session import engine
from sqlalchemy import text

update_sql = """
UPDATE approval_requests 
SET product_id = (payload->>'product_id')::integer,
    product_name = payload->>'product',
    current_qty = (payload->>'available_quantity')::numeric,
    min_qty = (payload->>'min_required')::numeric
WHERE action_type = 'low_stock_forecast_alert'
"""

with engine.connect() as conn:
    conn.execute(text(update_sql))
    conn.commit()
print("Data migration completed successfully.")
