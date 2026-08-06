import os
import fitz  # PyMuPDF لتحليل ملفات الـ PDF واستخراج الصور والنصوص
import requests
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- إعدادات واتساب والبوت ---
WHATSAPP_TOKEN = "YOUR_WHATSAPP_TOKEN"
PHONE_NUMBER_ID = "YOUR_PHONE_NUMBER_ID"
MY_WHATSAPP_NUMBER = "YOUR_PERSONAL_PHONE"

def send_whatsapp_message(to, text):
    """دالة لإرسال الرسائل النصية البسيطة"""
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"[WhatsApp] خطأ في إرسال النص: {e}")

def send_whatsapp_image(to, image_path, caption):
    """دالة لرفع صورة المنتج وإرسالها مع تفاصيل الطلب كتعليق"""
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    media_url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/media"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}"
    }
    try:
        with open(image_path, 'rb') as img_file:
            files = {
                'file': (os.path.basename(image_path), img_file, 'image/png'),
                'messaging_product': (None, 'whatsapp')
            }
            upload_res = requests.post(media_url, headers=headers, files=files)
            media_id = upload_res.json().get('id')
            
        if media_id:
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "image",
                "image": {
                    "id": media_id,
                    "caption": caption
                }
            }
            requests.post(url, json=payload, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"})
    except Exception as e:
        print(f"[WhatsApp] خطأ في إرسال الصورة: {e}")

def process_invoice_pdf(pdf_path):
    """النظام المستقل لقراءة الفاتورة، استخراج كل طلب وصورته وإرساله بشكل منفصل"""
    if not os.path.exists(pdf_path):
        print(f"[PDF Error] ملف الفاتورة غير موجود: {pdf_path}")
        return

    try:
        doc = fitz.open(pdf_path)
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            
            # استخراج صورة المنتج الموجودة في الصفحة
            image_list = page.get_images(full=True)
            saved_image_path = None
            
            if image_list:
                img_info = image_list[0]
                xref = img_info[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
                
                saved_image_path = f"invoice_item_{page_num}.{image_ext}"
                with open(saved_image_path, "wb") as img_file:
                    img_file.write(image_bytes)

            # متغيرات افتراضية للحقول المطلوب استخراجها
            order_id = "غير متوفر"
            product_name = "غير متوفر"
            quantity = "1"
            options = "لا يوجد"
            price = "غير متوفر"

            # تحليل النصوص المستخرجة من الفاتورة لتعبئة الحقول بدقة
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            for i, line in enumerate(lines):
                # البحث عن رقم الطلب
                if "رقم الطلب" in line or "Order" in line:
                    if i + 1 < len(lines):
                        order_id = lines[i+1]
                # البحث عن السعر
                if "SAR" in line or "ر.س" in line:
                    price = line

            # بناء رسالة التنسيق لكل طلب على حدة
            caption = (
                f"📦 *تفاصيل الطلب المستخرج*\n"
                f"----------------------------------\n"
                f"🔹 *رقم الطلب:* {order_id}\n"
                f"🛍️ *اسم المنتج:* {product_name}\n"
                f"🔢 *الكمية:* {quantity}\n"
                f"⚙️ *خيارات المنتج:* {options}\n"
                f"💰 *السعر:* {price}"
            )
            
            # إرسال الطلب مستقلًا مع صورته (إن وجدت)
            if saved_image_path and os.path.exists(saved_image_path):
                send_whatsapp_image(MY_WHATSAPP_NUMBER, saved_image_path, caption)
                os.remove(saved_image_path)
            else:
                send_whatsapp_message(MY_WHATSAPP_NUMBER, caption)
            
            time.sleep(1)
            
        doc.close()
        print("[Success] تمت معالجة وإرسال كافة طلبات الفاتورة بنجاح.")
        
    except Exception as e:
        print(f"[Parser Error] حدث خطأ أثناء تحليل ملف الـ PDF: {e}")

@app.route('/pdf-webhook', methods=['POST'])
def webhook():
    """مسار الـ Webhook لاستقبال ملفات أو روابط الفواتير"""
    data = request.json
    if data and 'pdf_url' in data:
        pdf_url = data['pdf_url']
        try:
            res = requests.get(pdf_url)
            pdf_path = "temp_downloaded_invoice.pdf"
            with open(pdf_path, 'wb') as f:
                f.write(res.content)
            
            process_invoice_pdf(pdf_path)
            
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
                
            return jsonify({"status": "success", "message": "تم معالجة الفاتورة وإرسال الطلبات"}), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({"status": "waiting_for_pdf"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
    
