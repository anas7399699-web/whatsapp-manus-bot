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

app = Flask(__name__)

# الإعدادات من بيئة Render
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

# ذاكرة مؤقتة لمنع التكرار في الجلسة الواحدة
processed_messages = set()

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
                        send_whatsapp_message(sender_id, "📍 الرياض:" if cat == "riyadh" else "📍 باقي المناطق:")
                        for m in msgs:
                            send_whatsapp_message(sender_id, m)
                            time.sleep(1)
        finally:
            if os.path.exists(path): os.remove(path)

    # مسار ملفات الـ PDF
    elif 'pdf' in mime_type or filename.endswith('.pdf'):
        handle_pdf_logic(sender_id, media_content)

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
            send_whatsapp_message(sender_id, "أهلاً أنس! أرسل ملف Excel لفرز الطلبات أو PDF لاستخراج البوالص.")
            
    except:
        pass
        
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
            
