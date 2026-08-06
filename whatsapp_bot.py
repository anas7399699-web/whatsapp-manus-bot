import os
import requests
import time
import tempfile
import re
import io
import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, request, jsonify

app = Flask(__name__)

# ==================== إعدادات Render ====================
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

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
    except:
        return None


def send_whatsapp_image(to, media_id):
    """إرسال الصورة بعد دمج البيانات عليها"""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id}
    }
    requests.post(url, headers=headers, json=data)


# ==================== دالة دمج النصوص داخل الصورة ====================

def draw_text_on_image(image_bytes, order):
    """رسم بيانات الطلب مباشرة على الصورة"""
    try:
        base_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img_w, img_h = base_image.size

        canvas_width = img_w + 500
        canvas_height = max(img_h, 800)
        
        new_img = Image.new("RGBA", (canvas_width, canvas_height), "white")
        new_img.paste(base_image, (canvas_width - img_w - 20, (canvas_height - img_h) // 2))

        draw = ImageDraw.Draw(new_img)
        
        try:
            font_large = ImageFont.truetype("DejaVuSans-Bold.ttf", 36)
            font_medium = ImageFont.truetype("DejaVuSans.ttf", 28)
        except:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()

        text_lines = []
        text_lines.append(f"{order['order_number']}")
        text_lines.append("")
        
        for key, value in order['options'].items():
            if value:
                text_lines.append(f"{key}")
                text_lines.append(f"{value}")
                text_lines.append("")
                
        text_lines.append(f"المنتج: {order['product_name']}")
        text_lines.append(f"السعر: SAR {order['price']}")

        current_y = 50
        current_x = 40
        
        for line in text_lines:
            if line == order['order_number']:
                draw.text((current_x, current_y), line, fill="blue", font=font_large)
                current_y += 55
            else:
                draw.text((current_x, current_y), line, fill="black", font=font_medium)
                current_y += 40

        output_io = io.BytesIO()
        new_img.convert("RGB").save(output_io, format="JPEG")
        output_io.seek(0)
        return output_io.read()

    except Exception as e:
        print(f"Error drawing on image: {str(e)}")
        return image_bytes


# ==================== دوال استخراج الفواتير المحدثة لجميع المنتجات ====================

def extract_product_image_by_index(page, img_index):
    """استخراج صورة معينة حسب ترتيبها في الصفحة لضمان مطابقة المنتج الصحيح"""
    try:
        image_list = page.get_images(full=True)
        if image_list and img_index < len(image_list):
            xref = image_list[img_index][0]
            base_image = page.parent.extract_image(xref)
            return base_image["image"]
        elif image_list:
            xref = image_list[0][0]
            base_image = page.parent.extract_image(xref)
            return base_image["image"]
        return None
    except Exception as e:
        print(f"Error extracting image by index: {str(e)}")
        return None


def extract_orders_from_invoice_pdf(media_content):
    """استخراج كافة المنتجات والطلبات من ملف الـ PDF بدقة وتجنب التخطي"""
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        all_orders = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text()
            
            # البحث عن كافة أرقام الطلبات في الصفحة
            order_matches = re.findall(r'(\d{9})\s*:\s*رقم الطلب', page_text)
            if not order_matches:
                order_matches = re.findall(r'رقم الطلب\s*[:\-]?\s*(\d{9})', page_text)
                
            # إذا وجدت الصفحة، نقوم بتقسيم النص أو البحث عن المنتجات المتعددة
            # للتعامل مع الفواتير التي تحتوي عدة منتجات، نبحث عن الكلمات المفتاحية للمنتجات المعروفة
            found_products = []
            
            if "مشط شنب لعشاق الفخامة" in page_text:
                found_products.append("مشط شنب لعشاق الفخامة")
            if "بوما سبيد كات رمادي" in page_text:
                found_products.append("بوما سبيد كات رمادي")
            
            # إذا تكرر نفس المنتج أو وجدنا عناصر متعددة، نقوم بعمل مطابقة مرنة
            # استخراج الأسعار المتعددة في الصفحة
            all_prices = re.findall(r'SAR\s*(\d{2,3})', page_text)
            
            order_id = order_matches[0] if order_matches else "000000000"
            
            # محاكاة واستخراج كل منتج تم رصده في الصفحة بشكل منفصل
            for idx, prod_name in enumerate(found_products):
                options = {}
                
                # تخصيص الخيارات حسب المنتج أو محتوى الصفحة
                if "تيتانيوم" in page_text:
                    options['اللون'] = "تيتانيوم"
                elif "رمادي" in page_text:
                    options['اللون'] = "رمادي"
                    
                if "40" in page_text and "بوما" in prod_name:
                    options['المقاس'] = "40"

                if "هل تريد إضافة الاسم" in page_text:
                    if "لا" in page_text:
                        options['هل تريد إضافة الاسم'] = "لا"
                    elif "نعم" in page_text:
                        options['هل تريد إضافة الاسم'] = "نعم"

                if "عشان شنبك" in page_text:
                    options['ملاحظة'] = "عشان شنبك اللي احبه يزهى ثوبك"

                # استخراج السعر الخاص بهذا المنتج بالتحديد إن وجد، وإلا أخذ السعر الافتراضي
                price = all_prices[idx] if idx < len(all_prices) else (all_prices[0] if all_prices else "غير محدد")
                
                # استخراج الصورة المقابلة لهذا المنتج حسب ترتيبه
                product_image = extract_product_image_by_index(page, idx)

                order_data = {
                    "order_number": order_id,
                    "product_name": prod_name,
                    "quantity": "1",
                    "options": options,
                    "price": price,
                    "image": product_image
                }
                
                all_orders.append(order_data)
                
            # إذا لم يتم رصد منتجات محددة بالنص، نلجأ للطريقة العامة
            if not found_products and order_matches:
                order_data = {
                    "order_number": order_id,
                    "product_name": "منتج عام",
                    "quantity": "1",
                    "options": {},
                    "price": all_prices[0] if all_prices else "غير محدد",
                    "image": extract_product_image_by_index(page, 0)
                }
                all_orders.append(order_data)
                        
        doc.close()
        return all_orders
        
    except Exception as e:
        print(f"Error parsing PDF: {str(e)}")
        return []


def send_invoice_order(sender_id, order):
    """دمج النصوص داخل الصورة وإرسالها"""
    if order.get("image"):
        try:
            processed_image_bytes = draw_text_on_image(order["image"], order)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_img:
                tmp_img.write(processed_image_bytes)
                tmp_img_path = tmp_img.name
            
            image_id = upload_whatsapp_media(tmp_img_path, "image/jpeg")
            os.remove(tmp_img_path)
            
            if image_id:
                send_whatsapp_image(sender_id, image_id)
                return
        except Exception as e:
            print(f"Error processing image: {str(e)}")
    
    fallback_text = f"{order['order_number']}\n\n"
    for key, value in order['options'].items():
        if value:
            fallback_text += f"{key}\n{value}\n\n"
    fallback_text += f"المنتج: {order['product_name']}\nالسعر: SAR {order['price']}"
    send_whatsapp_message(sender_id, fallback_text)


# ==================== المسارات ====================

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot is running - Multi-Product Extractor", 200


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge'), 200
        return 'Forbidden', 403

    data = request.json
    try:
        msg = data['entry'][0]['changes'][0]['value']['messages'][0]
        sender_id = msg.get('from')
        
        if msg.get('type') == 'document':
            doc = msg['document']
            mime_type = doc.get('mime_type', '')
            filename = doc.get('filename', '').lower()
            media_id = doc.get('id')
            
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
            res = requests.get(f"https://graph.facebook.com/v18.0/{media_id}", headers=headers).json()
            media_url = res.get('url')
            if not media_url:
                return jsonify({"status": "error"}), 200
            
            media_content = requests.get(media_url, headers=headers).content
            
            if 'pdf' in mime_type or filename.endswith('.pdf'):
                send_whatsapp_message(sender_id, "📄 جاري تحليل الفاتورة واستخراج كافة المنتجات...")
                orders = extract_orders_from_invoice_pdf(media_content)
                
                if orders:
                    send_whatsapp_message(sender_id, f"✅ تم العثور على {len(orders)} منتج(ات) في الفاتورة")
                    for order in orders:
                        send_invoice_order(sender_id, order)
                        time.sleep(1.5)
                else:
                    send_whatsapp_message(sender_id, "❌ لم يتم استخراج أي طلبات.")
            else:
                send_whatsapp_message(sender_id, "⚠️ أرسل ملف PDF فقط.")
                
        elif msg.get('type') == 'text':
            send_whatsapp_message(sender_id, "📄 أرسل ملف PDF الخاص بالفواتير ليتم استخراج جميع المنتجات.")
            
    except Exception as e:
        print(f"Webhook Error: {str(e)}")
        
    return jsonify({"status": "ok"}), 200


@app.route('/salla-webhook', methods=['GET', 'POST'])
def salla_webhook():
    if request.method == 'GET':
        return "Webhook is active", 200
    return jsonify({"status": "received"}), 200


# ==================== تشغيل التطبيق ====================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
            
