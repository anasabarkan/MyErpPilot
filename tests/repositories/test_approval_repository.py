from app.models.approval import ApprovalStatus
from app.repositories import approval_repository


class FakeQuery:
    def __init__(self):
        self.ordering = None
        self.offset_value = None
        self.limit_value = None

    def filter(self, *_args, **_kwargs):
        return self

    def count(self):
        return 0

    def order_by(self, *ordering):
        self.ordering = ordering
        return self

    def offset(self, value):
        self.offset_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def all(self):
        return []


class FakeDB:
    def __init__(self):
        self.query_obj = FakeQuery()

    def query(self, _model):
        return self.query_obj


def test_list_executed_requests_orders_by_latest_execution_first():
    db = FakeDB()

    items, total = approval_repository.list_requests(db, status=ApprovalStatus.executed)

    assert items == []
    assert total == 0
    ordering_text = [str(ordering) for ordering in db.query_obj.ordering]
    assert "approval_requests.executed_at DESC NULLS LAST" in ordering_text[0]
    assert "approval_requests.created_at DESC" in ordering_text[1]


def test_list_non_executed_requests_keeps_created_at_ordering():
    db = FakeDB()

    approval_repository.list_requests(db, status=ApprovalStatus.approved)

    ordering_text = [str(ordering) for ordering in db.query_obj.ordering]
    assert ordering_text == ["approval_requests.created_at DESC"]
