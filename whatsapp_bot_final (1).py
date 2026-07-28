import os
import requests
import time
import threading
import tempfile
import re
import fitz  # PyMuPDF لقراءة PDF
from PIL import Image
from flask import Flask, request, jsonify
from process_orders import process_excel_orders_to_list
import pandas as pd  # لإنشاء ملفات Excel

app = Flask(__name__)

# ==================== إعدادات Render ====================
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

# ==================== الذواكر المؤقتة ====================
processed_messages = set()  # لمنع تكرار معالجة نفس الرسالة
processed_salla_orders = set()  # لمنع تكرار معالجة نفس الطلب من سلة
user_temp_data = {}  # لتخزين بيانات الطلبات مؤقتاً لكل مستخدم
user_temp_expiry = {}  # لتخزين وقت انتهاء صلاحية البيانات لكل مستخدم

# ==================== إعدادات إضافية ====================
salla_lock = threading.Lock()
MY_WHATSAPP_NUMBER = "967739969981"

# ==================== دوال واتساب الأساسية ====================
def send_whatsapp_message(to, text):
    """إرسال رسالة نصية عبر واتساب"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    try:
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f"Error sending message: {e}")

def upload_whatsapp_media(file_path, mime_type):
    """رفع ملف (صورة أو مستند) إلى واتساب"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    files = {'file': (os.path.basename(file_path), open(file_path, 'rb'), mime_type)}
    data = {'messaging_product': 'whatsapp'}
    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        return response.json().get('id')
    except Exception as e:
        print(f"Error uploading media: {e}")
        return None

def send_whatsapp_image_with_caption(to, media_id, caption):
    """إرسال صورة مع تعليق عبر واتساب"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id, "caption": caption}
    }
    try:
        requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f"Error sending image: {e}")

# ==================== معالجة إشعارات سلة (Salla Webhook) ====================
def process_salla_webhook_async(order_data):
    """معالجة بيانات الطلب من سلة وإرسالها للواتساب"""
    try:
        order_id = order_data.get('id')
        
        # منع التكرار
        with salla_lock:
            if order_id in processed_salla_orders:
                return
            processed_salla_orders.add(order_id)

        # التحقق من الحالة (جاري التوصيل أو shipped)
        status = order_data.get('status', {})
        status_slug = status.get('slug', '')
        
        # قائمة الحالات التي نريد التفاعل معها
        target_statuses = ['out_for_delivery', 'shipped', 'delivered', 'completed']
        
        if status_slug not in target_statuses:
            print(f"تجاهل الطلب {order_id} لأن حالته هي: {status_slug}")
            return

        # استخراج البيانات
        customer = order_data.get('customer', {})
        recipient_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        recipient_mobile = customer.get('mobile', 'غير متوفر')
        
        shipping = order_data.get('shipping', {})
        address = shipping.get('address', {})
        full_address = f"{address.get('city', '')} - {address.get('district', '')} - {address.get('street', '')}".strip()
        
        # تنسيق الرسالة
        message = (
            f"📦 *طلب جديد جاري التوصيل*\n\n"
            f"🔢 رقم الطلب: {order_id}\n"
            f"👤 المستلم: {recipient_name}\n"
            f"📱 الجوال: {recipient_mobile}\n"
            f"📍 العنوان: {full_address}\n\n"
            f"✅ تم استلام البيانات تلقائياً من سلة."
        )

        # إرسال للواتساب
        send_whatsapp_message(MY_WHATSAPP_NUMBER, message)
        print(f"✅ تم إرسال بيانات الطلب {order_id} للواتساب.")

    except Exception as e:
        print(f"Error processing Salla webhook: {e}")

@app.route('/', methods=['GET'])
def home():
    return "✅ Bot is Live and Running!", 200

@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    """مسار استقبال إشعارات سلة"""
    if request.method == 'GET':
        return "✅ Webhook is active and ready for Salla!", 200

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"status": "no_data"}), 400

        try:
            event = data.get('event', '')
            raw_data = data.get('data', {})
            
            print(f"📢 وصل إشعار جديد من سلة! الحدث: {event}")

            # معالجة فقط أحداث تحديث الطلب
            if event in ["order.updated", "order.status.updated"]:
                threading.Thread(
                    target=process_salla_webhook_async,
                    args=(raw_data,)
                ).start()
            
            return jsonify({"status": "received"}), 200
        except Exception as e:
            print(f"Salla Webhook Route Error: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

# ==================== مسار واتساب (Webhook) ====================
@app.route('/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return challenge, 200
        return 'Forbidden', 403

    if request.method == 'POST':
        data = request.get_json()
        # هنا يتم وضع منطق معالجة رسائل واتساب الواردة (ملفات الإكسل والـ PDF)
        # كما كان في كودك الأصلي
        return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
