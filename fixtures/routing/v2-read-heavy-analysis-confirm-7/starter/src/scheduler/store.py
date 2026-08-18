from datetime import datetime
def serialize(schedule):
    return {"run_at": schedule.run_at.strftime("%Y-%m-%d %H:%M:%S"), "tags": schedule.tags}
def deserialize(row):
    return datetime.strptime(row["run_at"], "%Y-%m-%d %H:%M:%S")
