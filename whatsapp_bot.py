import os
import requests
import time
import threading
import tempfile
import re
import io
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

# ذاكرة مؤقتة لمنع تكرار في الجلسة الواحدة
processed_messages = set()
processed_salla_orders = set()

# قفل برمجى لضمان إرسال رسائل سلة واحدة تلو الأخرى بأمان دون تداخل
salla_lock = threading.Lock()

# رقم الواتساب الفعلي لاستقبال الطلبات التلقائية من سلة
MY_WHATSAPP_NUMBER = "967739969981"

# ==================== دوال واتساب ====================

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    requests.post(url, headers=headers, json=data )

def upload_whatsapp_media(file_path, mime_type):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {'file': (os.path.basename(file_path ), open(file_path, 'rb'), mime_type)}
    data = {'messaging_product': 'whatsapp'}
    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        return response.json().get('id')
    except:
        return None

def send_whatsapp_image_with_caption(to, media_id, caption):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id, "caption": caption}
    }
    requests.post(url, headers=headers, json=data )

def handle_pdf_logic(sender_id, media_content):
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        send_whatsapp_message(sender_id, f"📄 جاري استخراج {len(doc)} بوالص شحن... ⏳")
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text()
            order_match = re.search(r'\b(2\d{8})\b', text)
            order_number = order_match.group(1) if order_match else "غير محدد"
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_img:
                pix.save(tmp_img.name)
                image_id = upload_whatsapp_media(tmp_img.name, "image/png")
                if image_id:
                    send_whatsapp_image_with_caption(sender_id, image_id, f"📦 رقم الطلب: {order_number}")
                os.remove(tmp_img.name)
        
        send_whatsapp_message(sender_id, "✅ تم إرسال جميع البوالص بنجاح.")
    except Exception as e:
        print(f"PDF Error: {str(e)}")
        send_whatsapp_message(sender_id, "❌ حدث خطأ في معالجة ملف البوالص.")

def handle_document_async(sender_id, doc):
    mime_type = doc.get('mime_type', '')
    filename = doc.get('filename', '').lower()
    media_id = doc.get('id')
    
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res = requests.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers ).json()
    media_url = res.get('url')
    if not media_url: return
    
    media_content = requests.get(media_url, headers=headers).content

    if 'spreadsheet' in mime_type or filename.endswith(('.xlsx', '.xls')):
        send_whatsapp_message(sender_id, "📥 جاري فرز طلبات الإكسل لمتجر أليزا... ⏳")
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(media_content)
            path = tmp.name
        try:
            result = process_excel_orders_to_list(path)
            if result:
                for cat in ["riyadh", "others"]:
                    msgs = result.get(cat, [])
                    if msgs:
                        send_whatsapp_message(sender_id, "📍 *الرياض:*" if cat == "riyadh" else "📍 *باقي المناطق:*")
                        time.sleep(3)
                        for index, m in enumerate(msgs):
                            send_whatsapp_message(sender_id, m)
                            time.sleep(2)
                            if (index + 1) % 10 == 0:
                                time.sleep(6)
        except Exception as e:
            print(f"Excel processing error: {str(e)}")
            send_whatsapp_message(sender_id, "❌ حدث خطأ أثناء فرز ملف الإكسل.")
        finally:
            if os.path.exists(path): os.remove(path)

    elif 'pdf' in mime_type or filename.endswith('.pdf'):
        handle_pdf_logic(sender_id, media_content)

# ==================== دالة معالجة سلة (المدمجة الجديدة) ====================

def process_salla_webhook_async(raw_data):
    with salla_lock:
        try:
            # تدعم هيكل (order) الجديد وهيكل الشحنات (shipment) القديم
            data = raw_data.get('order', raw_data)
            order_id = str(data.get('id') or data.get('order_id') or 'غير متوفر')

            if order_id in processed_salla_orders:
                return
            processed_salla_orders.add(order_id)
            if len(processed_salla_orders) > 1000: processed_salla_orders.clear()

            customer = data.get('customer', {})
            name = customer.get('name') or f"{customer.get('first_name','')} {customer.get('last_name','')}".strip()
            mobile = str(customer.get('mobile') or customer.get('phone') or '').replace('+', '').replace(' ', '')
            
            addr = data.get('shipping_address') or data.get('address') or {}
            full_addr = f"{addr.get('city','')} - {addr.get('district','')} - {addr.get('street','')}".strip(' -')

            # استخراج المنتجات والمقاسات والألوان
            items_text = ""
            for item in data.get('items', []):
                opts = ", ".join([f"{o.get('name')}: {o.get('value')}" for o in item.get('options', [])])
                items_text += f"\n- {item.get('name')} (كمية: {item.get('quantity')}) [{opts}]"

            final_msg = f"العنوان / {full_addr}\nرقم الطلبية / {order_id}\nرقم المستلم / {mobile}\nاسم المستلم / {name}\nالمنتجات: {items_text}"

            print(f"[Salla] إرسال طلب {order_id} → {MY_WHATSAPP_NUMBER}")
            send_whatsapp_message(MY_WHATSAPP_NUMBER, final_msg)
            time.sleep(2)
        except Exception as e:
            print(f"[Salla] خطأ في المعالجة: {str(e)}")

# ==================== Keep-Alive ====================

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot is running", 200

def keep_alive():
    RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not RENDER_URL: return
    while True:
        try:
            time.sleep(600)
            requests.get(f"{RENDER_URL}/", timeout=10)
        except: pass

# ==================== مسار واتساب ====================

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge'), 200
        return 'Forbidden', 403

    data = request.json
    try:
        msg = data['entry'][0]['changes'][0]['value']['messages'][0]
        msg_id = msg.get('id')
        sender_id = msg.get('from')
        
        if msg_id in processed_messages: return jsonify({"status": "duplicate"}), 200
        processed_messages.add(msg_id)
        
        if msg.get('type') == 'document':
            threading.Thread(target=handle_document_async, args=(sender_id, msg['document'])).start()
        elif msg.get('type') == 'text':
            send_whatsapp_message(sender_id, "أهلاً أنس! أرسل ملف Excel أو PDF للمعالجة.")
    except: pass
    return jsonify({"status": "ok"}), 200

# ==================== مسار سلة Webhook ====================

@app.route('/salla-webhook', methods=['GET', 'POST', 'HEAD'])
def salla_webhook():
    if request.method in ['GET', 'HEAD']: return "Webhook is active", 200
    
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        event = str(data.get('event', '')).lower()
        raw_data = data.get('data', {})
        
        # التعديل: قبول أحداث الطلبات (order) وأحداث الشحن (shipment)
        if any(x in event for x in ['order.', 'shipment', 'carrier']):
            threading.Thread(target=process_salla_webhook_async, args=(raw_data,)).start()
            
        return jsonify({"status": "received"}), 200

if __name__ == '__main__':
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
    
