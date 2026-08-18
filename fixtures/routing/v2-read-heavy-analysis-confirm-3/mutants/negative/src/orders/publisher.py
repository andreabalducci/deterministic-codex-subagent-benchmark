def publish_pending(db, bus):
    for event in db.query("select * from outbox where sent_at is null"):
        bus.publish(event)
        db.execute("update outbox set sent_at=current_timestamp where id=?", (event.id,))
