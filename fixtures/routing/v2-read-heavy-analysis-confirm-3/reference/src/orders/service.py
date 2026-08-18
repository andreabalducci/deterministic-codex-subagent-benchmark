from .outbox import enqueue
def place_order(db, request):
    order = db.insert_order(request)
    db.commit()
    enqueue(db, order.id, "OrderPlaced")
    return order
