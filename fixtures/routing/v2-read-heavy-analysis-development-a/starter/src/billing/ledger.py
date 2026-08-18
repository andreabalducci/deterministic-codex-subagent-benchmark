entries = []
def post_charge(invoice_id, charge_id, amount):
    entries.append({"invoice": invoice_id, "charge": charge_id, "amount": amount})
