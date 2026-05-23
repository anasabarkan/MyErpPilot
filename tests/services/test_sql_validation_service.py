import pytest

from app.services.sql_validation_service import SQLValidationError, SQLValidationService
from app.services.text_to_sql_schema_service import build_schema_prompt


def test_validator_accepts_safe_select_and_adds_limit():
    service = SQLValidationService(max_rows=100)

    result = service.validate(
        """
        SELECT rp.name AS customer_name, SUM(so.amount_total) AS revenue
        FROM sale_order so
        JOIN res_partner rp ON rp.id = so.partner_id
        WHERE so.state IN ('sale', 'done')
        GROUP BY rp.name
        ORDER BY revenue DESC
        """
    )

    assert "LIMIT 100" in result.sql
    assert result.limit == 100


def test_validator_rejects_writes():
    service = SQLValidationService(max_rows=100)

    with pytest.raises(SQLValidationError):
        service.validate("UPDATE sale_order SET state = 'sale' WHERE id = 1")


def test_validator_rejects_unknown_table():
    service = SQLValidationService(max_rows=100)

    with pytest.raises(SQLValidationError):
        service.validate("SELECT id FROM res_users LIMIT 10")


def test_validator_rejects_unknown_column():
    service = SQLValidationService(max_rows=100)

    with pytest.raises(SQLValidationError):
        service.validate("SELECT password FROM res_partner LIMIT 10")


def test_validator_rejects_multi_statement():
    service = SQLValidationService(max_rows=100)

    with pytest.raises(SQLValidationError):
        service.validate("SELECT id FROM sale_order LIMIT 10; SELECT id FROM res_partner LIMIT 10")


def test_validator_rejects_oversized_limit():
    service = SQLValidationService(max_rows=100)

    with pytest.raises(SQLValidationError):
        service.validate("SELECT id FROM sale_order LIMIT 1000")


def test_validator_accepts_sales_revenue_by_product_category():
    service = SQLValidationService(max_rows=100)

    result = service.validate(
        """
        SELECT pc.complete_name AS category_name, SUM(sol.price_total) AS revenue
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        JOIN product_product pp ON pp.id = sol.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN product_category pc ON pc.id = pt.categ_id
        WHERE so.state IN ('sale', 'done') AND sol.display_type IS NULL
        GROUP BY pc.complete_name
        ORDER BY revenue DESC
        LIMIT 20
        """
    )

    assert result.limit == 20


def test_validator_accepts_invoice_revenue_by_month_and_customer():
    service = SQLValidationService(max_rows=100)

    result = service.validate(
        """
        SELECT DATE_TRUNC('month', am.invoice_date) AS invoice_month,
               rp.name AS customer_name,
               SUM(am.amount_total) AS revenue
        FROM account_move am
        JOIN res_partner rp ON rp.id = am.partner_id
        WHERE am.state = 'posted'
          AND am.move_type = 'out_invoice'
        GROUP BY invoice_month, rp.name
        ORDER BY invoice_month DESC, revenue DESC
        LIMIT 50
        """
    )

    assert result.limit == 50


def test_validator_accepts_purchase_spend_by_supplier():
    service = SQLValidationService(max_rows=100)

    result = service.validate(
        """
        SELECT rp.name AS supplier_name, SUM(pol.price_total) AS spend
        FROM purchase_order_line pol
        JOIN purchase_order po ON po.id = pol.order_id
        JOIN res_partner rp ON rp.id = po.partner_id
        WHERE po.state IN ('purchase', 'done') AND pol.display_type IS NULL
        GROUP BY rp.name
        ORDER BY spend DESC
        LIMIT 25
        """
    )

    assert result.limit == 25


def test_validator_accepts_stock_on_hand_by_product_category_and_location():
    service = SQLValidationService(max_rows=100)

    result = service.validate(
        """
        SELECT COALESCE(pt.name->>'en_US', pt.name::text) AS product_name,
               pc.complete_name AS category_name,
               sl.complete_name AS location_name,
               SUM(sq.quantity - sq.reserved_quantity) AS available_quantity
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        JOIN product_product pp ON pp.id = sq.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN product_category pc ON pc.id = pt.categ_id
        WHERE sl.usage = 'internal'
        GROUP BY product_name, pc.complete_name, sl.complete_name
        ORDER BY available_quantity DESC
        LIMIT 50
        """
    )

    assert result.limit == 50


def test_validator_accepts_stock_movement_by_warehouse():
    service = SQLValidationService(max_rows=100)

    result = service.validate(
        """
        SELECT sw.name AS warehouse_name,
               sm.state,
               SUM(sm.quantity) AS moved_quantity
        FROM stock_move sm
        JOIN stock_warehouse sw ON sw.id = sm.warehouse_id
        WHERE sm.state = 'done'
        GROUP BY sw.name, sm.state
        ORDER BY moved_quantity DESC
        LIMIT 50
        """
    )

    assert result.limit == 50


def test_validator_rejects_sensitive_operational_tables():
    service = SQLValidationService(max_rows=100)

    with pytest.raises(SQLValidationError):
        service.validate("SELECT id, login FROM res_users LIMIT 10")

    with pytest.raises(SQLValidationError):
        service.validate("SELECT id, datas FROM ir_attachment LIMIT 10")


def test_schema_prompt_includes_core_erp_tables_and_joins():
    prompt = build_schema_prompt()

    assert "account_move_line" in prompt
    assert "purchase_order_line" in prompt
    assert "stock_move" in prompt
    assert "product_category" in prompt
    assert "stock_quant.location_id = stock_location.id" in prompt
    assert "account_move_line.move_id = account_move.id" in prompt
