import os
import requests
import time
import threading
import tempfile
import re
import fitz  # PyMuPDF
from PIL import Image
from flask import Flask, request, jsonify
from process_orders import process_excel_orders_to_list
from datetime import datetime

app = Flask(__name__)

# الإعدادات من بيئة Render
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')
MY_WHATSAPP_NUMBER = "967739969981"

# الذاكرة المؤقتة
processed_messages = set()
processed_salla_orders = set()
salla_lock = threading.Lock()

# ==================== دوال واتساب ====================

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    requests.post(url, headers=headers, json=data)

def upload_whatsapp_media(file_path, mime_type):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {'file': (os.path.basename(file_path), open(file_path, 'rb'), mime_type)}
    data = {'messaging_product': 'whatsapp'}
    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        return response.json().get('id')
    except: return None

def send_whatsapp_image_with_caption(to, media_id, caption):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"id": media_id, "caption": caption}}
    requests.post(url, headers=headers, json=data)

# ==================== معالجة PDF و Excel ====================

def handle_pdf_logic(sender_id, media_content):
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        send_whatsapp_message(sender_id, f"📄 جاري استخراج {len(doc)} بوالص شحن...")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text()
            order_match = re.search(r'\b(2\d{8})\b', text)
            order_number = order_match.group(1) if order_match else "غير محدد"
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_img:
                pix.save(tmp_img.name)
                image_id = upload_whatsapp_media(tmp_img.name, "image/png")
                if image_id: send_whatsapp_image_with_caption(sender_id, image_id, f"📦 رقم الطلب: {order_number}")
                os.remove(tmp_img.name)
        send_whatsapp_message(sender_id, "✅ تم إرسال جميع البوالص.")
    except Exception as e: print(f"PDF Error: {e}")

def handle_document_async(sender_id, doc):
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res = requests.get(f"https://graph.facebook.com/v18.0/{doc.get('id')}", headers=headers).json()
    media_url = res.get('url')
    if not media_url: return
    media_content = requests.get(media_url, headers=headers).content
    
    if 'spreadsheet' in doc.get('mime_type', ''):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(media_content)
            path = tmp.name
        try:
            result = process_excel_orders_to_list(path)
            for cat in ["riyadh", "others"]:
                if result.get(cat):
                    send_whatsapp_message(sender_id, f"📍 *{'الرياض' if cat == 'riyadh' else 'باقي المناطق'}:*")
                    for m in result.get(cat, []): send_whatsapp_message(sender_id, m)
        finally: os.remove(path)
    elif 'pdf' in doc.get('mime_type', ''): handle_pdf_logic(sender_id, media_content)

# ==================== معالجة سلة (المحدثة) ====================

def process_salla_webhook_async(raw_data):
    with salla_lock:
        try:
            data = raw_data.get('order', raw_data)
            order_id = str(data.get('id') or data.get('order_id') or 'غير متوفر')
            
            if order_id in processed_salla_orders: return
            processed_salla_orders.add(order_id)

            customer = data.get('customer', {})
            name = customer.get('name') or f"{customer.get('first_name','')} {customer.get('last_name','')}".strip()
            mobile = str(customer.get('mobile') or customer.get('phone') or '').replace('+', '').replace(' ', '')
            
            addr = data.get('shipping_address') or data.get('address') or {}
            full_addr = f"{addr.get('city','')} - {addr.get('district','')} - {addr.get('street','')}".strip(' -')

            items_text = ""
            for item in data.get('items', []):
                opts = ", ".join([f"{o.get('name')}: {o.get('value')}" for o in item.get('options', [])])
                items_text += f"\n- {item.get('name')} (كمية: {item.get('quantity')}) [{opts}]"

            final_msg = f"العنوان / {full_addr}\nرقم الطلبية / {order_id}\nرقم المستلم / {mobile}\nاسم المستلم / {name}\nالمنتجات: {items_text}"
            
            send_whatsapp_message(MY_WHATSAPP_NUMBER, final_msg)
        except Exception as e: print(f"[Salla] Error: {e}")

# ==================== المسارات ====================

@app.route('/salla-webhook', methods=['GET', 'POST', 'HEAD'])
def salla_webhook():
    if request.method in ['GET', 'HEAD']: return "Webhook is active", 200
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        event = str(data.get('event', '')).lower()
        if any(x in event for x in ['order.', 'shipment', 'carrier']):
            threading.Thread(target=process_salla_webhook_async, args=(data.get('data', {}),)).start()
        return jsonify({"status": "received"}), 200

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return request.args.get('hub.challenge', '') if request.args.get('hub.verify_token') == VERIFY_TOKEN else 'Forbidden', 200
    data = request.json
    try:
        msg = data['entry'][0]['changes'][0]['value']['messages'][0]
        if msg.get('type') == 'document': threading.Thread(target=handle_document_async, args=(msg.get('from'), msg['document'])).start()
    except: pass
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
        
