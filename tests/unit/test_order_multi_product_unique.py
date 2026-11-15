from app.domain.order.src.model.order import Order


def test_active_order_uniqueness_includes_selected_product():
    index = next(
        table_index
        for table_index in Order.__table__.indexes
        if table_index.name == "uq_order_active_user_plan_product"
    )

    assert [column.name for column in index.columns] == [
        "user_id",
        "plan_id",
        "product_type",
    ]
    assert index.unique is True
