def key_for(message):
    return f'invoice:{message["invoice_id"]}:attempt:{message["attempt"]}'
