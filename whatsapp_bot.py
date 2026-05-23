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

# ذاكرة مؤقتة لمنع التكرار في الجلسة الواحدة
processed_messages = set()
processed_salla_orders = set()  # لمنع تكرار معالجة نفس الطلب القادم من سلة لحظياً

# قفل برمجى لضمان إرسال رسائل سلة واحدة تلو الأخرى بأمان دون تداخل عند التحديد الجماعي
salla_lock = threading.Lock()

# رقم الواتساب الفعلي الخاص بك (أنس) لاستقبل الطلبات التلقائية المباشرة من سلة
MY_WHATSAPP_NUMBER = "967739969981"

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
    except:
        return None

def send_whatsapp_image_with_caption(to, media_id, caption):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {
            "id": media_id,
            "caption": caption
        }
    }
    requests.post(url, headers=headers, json=data)

def handle_pdf_logic(sender_id, media_content):
    """تحويل بوالص الـ PDF لصور واستخراج رقم الطلب بذكاء"""
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        send_whatsapp_message(sender_id, f"📄 جاري استخراج {len(doc)} بوالص شحن... ⏳")
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text()
            
            # البحث عن رقم يبدأ بـ 2 ومكون من 9 أرقام في أي مكان بالصفحة
            order_match = re.search(r'\b(2\d{8})\b', text)
            order_number = order_match.group(1) if order_match else "غير محدد"

            # تحويل الصفحة لصورة بجودة عالية
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_img:
                pix.save(tmp_img.name)
                image_id = upload_whatsapp_media(tmp_img.name, "image/png")
                
                if image_id:
                    caption_text = f"📦 رقم الطلب: {order_number}"
                    send_whatsapp_image_with_caption(sender_id, image_id, caption_text)
                
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
    res = requests.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers).json()
    media_url = res.get('url')
    if not media_url: return
    
    media_content = requests.get(media_url, headers=headers).content

    # مسار ملفات الإكسل
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
                        # إرسال عنوان المنطقة أولاً
                        send_whatsapp_message(sender_id, "📍 *الرياض:*" if cat == "riyadh" else "📍 *باقي المناطق:*")
                        time.sleep(3) # مهلة أمان بعد العنوان
                        
                        # إرسال كل طلب في رسالة منفصلة تماماً مع نظام الحماية من الحظر للأعداد الكبيرة فوق 50 طلباً
                        for index, m in enumerate(msgs):
                            send_whatsapp_message(sender_id, m)
                            
                            # مهلة أمان أساسية (2 ثانية) بين كل رسالة وأخرى لضمان وصولها بالترتيب
                            time.sleep(2)
                            
                            # نظام الاستراحة الذكي: كل 10 رسائل متتالية، انتظر 6 ثوانٍ إضافية لتتنفس السيرفرات
                            if (index + 1) % 10 == 0:
                                print(f"DEBUG: Sent {index + 1} messages, taking a short break...")
                                time.sleep(6)
                                
        except Exception as e:
            print(f"Excel processing error: {str(e)}")
            send_whatsapp_message(sender_id, "❌ حدث خطأ أثناء فرز ملف الإكسل.")
        finally:
            if os.path.exists(path): os.remove(path)

    # مسار ملفات الـ PDF
    elif 'pdf' in mime_type or filename.endswith('.pdf'):
        handle_pdf_logic(sender_id, media_content)

# دالة معالجة طلبات الـ Webhook القادمة من سلة في الخلفية مع ميزة الطابور الآمن للكميات الضخمة
def process_salla_webhook_async(order_data):
    # استخدام الـ Lock لضمان خروج رسائل التحديد الجماعي واحدة تلو الأخرى بالتسلسل لتفادي الحظر
    with salla_lock:
        try:
            order_id = order_data.get('id', 'غير متوفر')
            
            # منع معالجة الطلب نفسه بشكل مكرر ومفاجئ
            if order_id in processed_salla_orders:
                return
            processed_salla_orders.add(order_id)
            if len(processed_salla_orders) > 1000: processed_salla_orders.clear()

            # 1. استخراج المدينة
            shipping_info = order_data.get('shipping', {})
            city = shipping_info.get('address', {}).get('city', '')
            if not city:
                city = order_data.get('customer', {}).get('city', '')

            # 2. استخراج العنوان بالتفصيل
            address_parts = []
            street = shipping_info.get('address', {}).get('street', '')
            district = shipping_info.get('address', {}).get('district', '')
            if street: address_parts.append(street)
            if district: address_parts.append(f"حي {district}")
            
            short_address = shipping_info.get('address', {}).get('short_address')
            if short_address: address_parts.append(f"العنوان المختصر {short_address}")
            
            full_address = " ".join(address_parts) if address_parts else city

            # 3. حل مشكلة الإهداء (بيانات المستلم المهدى إليه ضد العميل المشتري المباشر)
            recipient_name = shipping_info.get('receiver', {}).get('name')
            recipient_mobile = shipping_info.get('receiver', {}).get('phone')

            if not recipient_name:
                recipient_name = order_data.get('customer', {}).get('first_name', '') + " " + order_data.get('customer', {}).get('last_name', '')
            if not recipient_mobile:
                recipient_mobile = order_data.get('customer', {}).get('mobile', '')

            mobile_str = str(recipient_mobile).strip()
            if mobile_str.startswith('5') and len(mobile_str) == 9:
                mobile_str = '966' + mobile_str
            elif mobile_str.startswith('05') and len(mobile_str) == 10:
                mobile_str = '966' + mobile_str[1:]
            elif mobile_str.startswith('+'):
                mobile_str = mobile_str.replace('+', '')

            # 4. صياغة الرسالة النهائية بنفس تنسيقك المعتاد
            final_msg = f"العنوان / {full_address}\nرقم الطلبية/ {order_id}\nرقم المستلم / +{mobile_str}\nاسم المستلم/ {recipient_name.strip()}"
            
            # إرسال الرسالة إلى رقم هاتفك الفعلي المسجل في الكود أعلاه
            send_whatsapp_message(MY_WHATSAPP_NUMBER, final_msg)
            
            # مهلة أمان إجبارية (ثانيتين) بين كل طلب وطلب لحمايتك عند النقل الجماعي للطلبات
            time.sleep(2)

        except Exception as e:
            print(f"Async Salla Process Error: {str(e)}")

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
        
        # --- حل مشكلة التكرار القديم: الفلترة الزمنية ---
        msg_timestamp = int(msg.get('timestamp')) 
        current_time = int(time.time())
        
        # إذا كانت الرسالة أقدم من 5 دقائق (300 ثانية)، يتم تجاهلها
        if (current_time - msg_timestamp) > 300:
            return jsonify({"status": "ignored_old_message"}), 200
        # ---------------------------------------------

        if msg_id in processed_messages: 
            return jsonify({"status": "duplicate"}), 200
        
        processed_messages.add(msg_id)
        if len(processed_messages) > 1000: processed_messages.clear()

        if msg.get('type') == 'document':
            threading.Thread(target=handle_document_async, args=(sender_id, msg['document'])).start()
        elif msg.get('type') == 'text':
            # تحديث نص الرسالة الترحيبية لتعكس التعديلات الأخيرة
            send_whatsapp_message(sender_id, "أهلاً أنس! أرسل ملف Excel لفرز طلبات (قيد التنفيذ وجاري التوصيل) في رسائل منفصلة، أو PDF لاستخراج البوالص.")
            
    except:
        pass
        
    return jsonify({"status": "ok"}), 200

# 🌐 مسار الـ Webhook المخصص لاستقبال ربط متجر سلة بشكل تلقائي ومباشر وآمن 🌐
@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    # 1. التجاوب مع طلب سلة التجريبي والتحقق من صحة الرابط (GET)
    if request.method == 'GET':
        print("Salla webhook verification test received via GET.")
        return "Webhook is active", 200

    # 2. استقبال بيانات التحديث التلقائي للطلبات (POST)
    if request.method == 'POST':
        data = request.json
        if not data:
            return jsonify({"status": "no_data"}), 400

        try:
            event = data.get('event')
            order_data = data.get('data', {})
            order_id = order_data.get('id', 'غير متوفر')
            status = order_data.get('status', {}).get('id')

            # الفلترة: نشتغل فقط إذا تحول الطلب إلى "جاري التوصيل"
            if status == 'delivering' or event == 'order.status.updated':
                # فلتر حماية زمني لمنع معالجة وإرسال المئات من الطلبات القديمة جداً الموجودة بالقسم سابقاً
                updated_at_str = order_data.get('updated_at', '')
                if updated_at_str:
                    try:
                        updated_at = datetime.strptime(updated_at_str, "%Y-%m-%d %H:%M:%S")
                        current_time = datetime.now()
                        time_diff = (current_time - updated_at).total_seconds()
                        # إذا كان التحديث أقدم من 5 دقائق (300 ثانية)، يتم تجاهله تماماً كحماية
                        if time_diff > 300:
                            return jsonify({"status": "ignored_old_order"}), 200
                    except:
                        pass

                # نقل معالجة الطلب التلقائي إلى الخلفية لتطبيق نظام الطابور الآمن لحمايتك من الحظر
                threading.Thread(target=process_salla_webhook_async, args=(order_data,)).start()

        except Exception as e:
            print(f"Salla Webhook Route Error: {str(e)}")
            
        return jsonify({"status": "received"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
