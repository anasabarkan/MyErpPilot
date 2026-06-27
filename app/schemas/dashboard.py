"""
Pydantic schemas for dashboard summary responses.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.product_inventory_state import ProductInventoryStatus


class DashboardMetrics(BaseModel):
    customers: int = 0
    products: int = 0
    quotations: int = 0
    sales_orders: int = 0
    invoices: int = 0
    revenue_total: float = 0.0
    companies: int = 0
    purchase_orders: int = 0


class DashboardRecentSalesOrder(BaseModel):
    id: int | None = None
    name: str
    customer: str = "Unknown"
    amount_total: float = 0.0
    state: str = "unknown"
    date_order: str | None = None


class DashboardTopCustomer(BaseModel):
    id: int | None = None
    name: str
    customer_rank: int = 0


class DashboardSalesByMonthPoint(BaseModel):
    month: str
    revenue: float = 0.0


class DashboardTopRevenuePoint(BaseModel):
    name: str
    revenue: float = 0.0
    short_name: str | None = None
    full_name: str | None = None


class DashboardInvoiceStatusPoint(BaseModel):
    label: str
    value: float = 0.0


class DashboardLowStockItem(BaseModel):
    name: str
    qty: float = 0.0
    min_required: float = 0.0
    shortage: float = 0.0


class DashboardLowStockRuleItem(BaseModel):
    product: str
    quantity: float = 0.0
    min_required: float = 0.0
    shortage: float = 0.0


class DashboardCharts(BaseModel):
    sales_by_month: list[DashboardSalesByMonthPoint] = Field(default_factory=list)
    top_customers: list[DashboardTopRevenuePoint] = Field(default_factory=list)
    top_products: list[DashboardTopRevenuePoint] = Field(default_factory=list)
    invoice_status: list[DashboardInvoiceStatusPoint] = Field(default_factory=list)
    low_stock_items: list[DashboardLowStockItem] = Field(default_factory=list)


class DashboardSummaryResponse(BaseModel):
    source: str = "odoo"
    status: str = "connected"
    database: str
    metrics: DashboardMetrics
    charts: DashboardCharts
    low_stock: list[DashboardLowStockRuleItem] = Field(default_factory=list)
    recent_sales_orders: list[DashboardRecentSalesOrder] = Field(default_factory=list)
    top_customers: list[DashboardTopCustomer] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DashboardAnalyticsMetrics(BaseModel):
    revenue: float = 0.0
    sales_orders: int = 0
    invoices: int = 0
    unpaid_amount: float = 0.0
    purchase_spend: float = 0.0
    average_order_value: float = 0.0
    customers: int = 0
    products: int = 0


class DashboardAnalyticsTrendPoint(BaseModel):
    period: str
    revenue: float = 0.0
    invoices: float = 0.0
    purchases: float = 0.0


class DashboardAnalyticsValuePoint(BaseModel):
    name: str
    value: float = 0.0
    short_name: str | None = None
    full_name: str | None = None


class DashboardAnalyticsInventoryPoint(BaseModel):
    product: str
    location: str = "Unknown"
    quantity: float = 0.0
    reserved_quantity: float = 0.0
    available_quantity: float = 0.0


class DashboardAnalyticsCharts(BaseModel):
    revenue_trend: list[DashboardAnalyticsTrendPoint] = Field(default_factory=list)
    top_customers: list[DashboardAnalyticsValuePoint] = Field(default_factory=list)
    top_products: list[DashboardAnalyticsValuePoint] = Field(default_factory=list)
    product_categories: list[DashboardAnalyticsValuePoint] = Field(default_factory=list)
    invoice_status: list[DashboardAnalyticsValuePoint] = Field(default_factory=list)
    sales_order_status: list[DashboardAnalyticsValuePoint] = Field(default_factory=list)
    purchase_order_status: list[DashboardAnalyticsValuePoint] = Field(default_factory=list)
    purchase_spend: list[DashboardAnalyticsValuePoint] = Field(default_factory=list)
    stock_availability: list[DashboardAnalyticsInventoryPoint] = Field(default_factory=list)
    low_stock_items: list[DashboardLowStockItem] = Field(default_factory=list)


class DashboardAnalyticsResponse(BaseModel):
    source: str = "odoo"
    status: str = "connected"
    database: str
    range: str
    generated_at: str
    metrics: DashboardAnalyticsMetrics
    charts: DashboardAnalyticsCharts
    warnings: list[str] = Field(default_factory=list)


class DashboardDiagnosticsActiveSource(BaseModel):
    name: str
    url: str
    database: str
    username: str


class DashboardDiagnosticsMetricTest(BaseModel):
    ok: bool
    value: float = 0
    strategy: str
    error: str | None = None


class DashboardDiagnosticsMetricTests(BaseModel):
    customers: DashboardDiagnosticsMetricTest
    products_product: DashboardDiagnosticsMetricTest
    products_template: DashboardDiagnosticsMetricTest
    quotations: DashboardDiagnosticsMetricTest
    sales_orders: DashboardDiagnosticsMetricTest
    invoices: DashboardDiagnosticsMetricTest


class DashboardDiagnosticsResponse(BaseModel):
    active_source: DashboardDiagnosticsActiveSource
    models: dict[str, bool]
    metric_tests: DashboardDiagnosticsMetricTests
    warnings: list[str] = Field(default_factory=list)


class DashboardProductInventoryStateItem(BaseModel):
    odoo_product_id: int
    product_name: str
    company_id: int | None = None
    quantity: float = 0.0
    reserved_quantity: float = 0.0
    available_quantity: float = 0.0
    state: ProductInventoryStatus
    last_synced_at: datetime
    last_state_changed_at: datetime

    class Config:
        from_attributes = True


class DashboardProductInventoryStatesResponse(BaseModel):
    items: list[DashboardProductInventoryStateItem] = Field(default_factory=list)
    total: int = 0
