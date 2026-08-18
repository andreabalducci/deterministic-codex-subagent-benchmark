from .ledger import post_charge

def handle(message, gateway):
    charge = gateway.capture(message["payment_id"], message["amount"])
    post_charge(message["invoice_id"], charge.id, message["amount"])
    message.ack()
