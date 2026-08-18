def enqueue(db, entity_id, kind):
    db.execute("insert into outbox(entity_id, kind) values (?, ?)", (entity_id, kind))
