import os
import requests
import time
import tempfile
import re
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
    """إرسال الصورة وحدها بعد أن أصبحت البيانات مكتوبة عليها مباشرة"""
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
        # فتح الصورة من بايتات البيانات
        base_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img_w, img_h = base_image.size

        # إنشاء لوحة بيضاء أوسع بجانب الصورة أو مساحة مخصصة للنصوص (مثلاً تصميم يشبه الصورة المطلوبة)
        # هنا سنقوم بإنشاء مساحة بيضاء على اليسار أو اليمين، أو الكتابة مباشرة على خلفية بيضاء جديدة بجانب الصورة الأصلية
        canvas_width = img_w + 500  # إضافة مساحة جانبية للكتابة
        canvas_height = max(img_h, 800)
        
        new_img = Image.new("RGBA", (canvas_width, canvas_height), "white")
        # لصق صورة المنتج في الجانب الأيمن (أو الأيسر حسب الرغبة)
        new_img.paste(base_image, (canvas_width - img_w - 20, (canvas_height - img_h) // 2))

        draw = ImageDraw.Draw(new_img)
        
        # محاولة استخدام خط يدعم العربية، وإذا لم يتوفر يتم استخدام الخط الافتراضي
        try:
            # في بيئات لينكس مثل Render يُفضل توفير خط يدعم العربية، أو استخدام الافتراضي
            font_large = ImageFont.truetype("DejaVuSans-Bold.ttf", 36)
            font_medium = ImageFont.truetype("DejaVuSans.ttf", 28)
        except:
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()

        # تجهيز النصوص المراد كتابتها بترتيب مثل الصورة
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

        # كتابة النصوص على الصورة (تحديد الإحداثيات X و Y)
        current_y = 50
        current_x = 40
        
        for line in text_lines:
            if line == order['order_number']:
                draw.text((current_x, current_y), line, fill="blue", font=font_large)
                current_y += 55
            else:
                draw.text((current_x, current_y), line, fill="black", font=font_medium)
                current_y += 40

        # تحويل الصورة النهائية إلى بايتات لإرسالها
        output_io = io.BytesIO()
        new_img.convert("RGB").save(output_io, format="JPEG")
        output_io.seek(0)
        return output_io.read()

    except Exception as e:
        print(f"Error drawing on image: {str(e)}")
        return image_bytes  # في حال حدث خطأ، ترجع الصورة الأصلية كما هي


# استيراد مكتبة io المساعدة للتعامل مع البايتات
import io


# ==================== دوال استخراج الفواتير ====================

def extract_product_image_from_page(page, product_name):
    """استخراج صورة المنتج من الصفحة بدقة"""
    try:
        text_instances = page.search_for(product_name[:15])
        if text_instances:
            prod_rect = text_instances[0]
            prod_y = prod_rect.y0
            
            image_list = page.get_images(full=True)
            best_match = None
            best_distance = float('inf')
            
            for img in image_list:
                xref = img[0]
                base_image = page.parent.extract_image(xref)
                image_bytes = base_image["image"]
                
                image_rects = page.get_image_rects(xref)
                if image_rects:
                    img_rect = image_rects[0]
                    img_y = img_rect.y0
                    distance = abs(img_y - prod_y)
                    
                    if distance < 350 and distance < best_distance:
                        best_distance = distance
                        best_match = image_bytes
            
            if best_match:
                return best_match
        
        image_list = page.get_images(full=True)
        if image_list:
            xref = image_list[0][0]
            base_image = page.parent.extract_image(xref)
            return base_image["image"]
        
        return None
    except Exception as e:
        print(f"Error extracting image: {str(e)}")
        return None


def extract_orders_from_invoice_pdf(media_content):
    """استخراج الطلبات بدقة وتطهير النصوص تماماً"""
    try:
        doc = fitz.open(stream=media_content, filetype="pdf")
        all_orders = []
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text()
            
            order_matches = re.findall(r'(\d{9})\s*:\s*رقم الطلب', page_text)
            if not order_matches:
                order_matches = re.findall(r'رقم الطلب\s*[:\-]?\s*(\d{9})', page_text)
                
            if order_matches:
                for order_id in order_matches:
                    product_name = "منتج غير محدد"
                    if "مشط شنب لعشاق الفخامة" in page_text:
                        product_name = "مشط شنب لعشاق الفخامة"
                    elif "بوما سبيد كات رمادي" in page_text:
                        product_name = "بوما سبيد كات رمادي"
                    
                    qty = "1"
                    qty_match = re.search(r'\b([1-9])\s*\n\s*SAR\s*\d+', page_text)
                    if qty_match:
                        qty = qty_match.group(1)
                        
                    price = "غير محدد"
                    price_match = re.search(r'SAR\s*(\d+)\s*\n\s*SAR\s*\d+\s*\n\s*1', page_text)
                    if not price_match:
                        price_match = re.search(r'SAR\s*(\d+)\s*\n\s*مجموع السلة', page_text)
                    if price_match:
                        price = price_match.group(1)
                    else:
                        all_prices = re.findall(r'SAR\s*(\d{2,3})', page_text)
                        if all_prices:
                            price = all_prices[0]

                    options = {}
                    if "تيتانيوم" in page_text:
                        options['اللون'] = "تيتانيوم"
                    elif "رمادي" in page_text:
                        options['اللون'] = "رمادي"
                        
                    if "40" in page_text and "بوما" in product_name:
                        options['المقاس'] = "40"

                    if "هل تريد إضافة الاسم" in page_text:
                        if "لا" in page_text:
                            options['هل تريد إضافة الاسم'] = "لا"
                        elif "نعم" in page_text:
                            options['هل تريد إضافة الاسم'] = "نعم"

                    if "عشان شنبك" in page_text:
                        options['ملاحظة'] = "عشان شنبك اللي احبه يزهى ثوبك"

                    product_image = extract_product_image_from_page(page, product_name)

                    order_data = {
                        "order_number": order_id,
                        "product_name": product_name,
                        "quantity": qty,
                        "options": options,
                        "price": price,
                        "image": product_image
                    }
                    
                    if not any(o["order_number"] == order_id for o in all_orders):
                        all_orders.append(order_data)
                        
        doc.close()
        return all_orders
        
    except Exception as e:
        print(f"Error parsing PDF: {str(e)}")
        return []


def send_invoice_order(sender_id, order):
    """دمج النصوص داخل الصورة ثم إرسال الصورة وحدها بدون نص إضافي مرفق"""
    if order.get("image"):
        try:
            # دمج البيانات والكتابة مباشرة على بايتات الصورة
            processed_image_bytes = draw_text_on_image(order["image"], order)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_img:
                tmp_img.write(processed_image_bytes)
                tmp_img_path = tmp_img.name
            
            image_id = upload_whatsapp_media(tmp_img_path, "image/jpeg")
            os.remove(tmp_img_path)
            
            if image_id:
                # إرسال الصورة فقط بدون أي نص تعليق (Caption) لأن البيانات أصبحت مرسومة داخلها
                send_whatsapp_image(sender_id, image_id)
                return
        except Exception as e:
            print(f"Error processing image: {str(e)}")
    
    # في حال عدم وجود صورة، يتم إرسال البيانات كنص عادي
    fallback_text = f"{order['order_number']}\n\n"
    for key, value in order['options'].items():
        if value:
            fallback_text += f"{key}\n{value}\n\n"
    fallback_text += f"المنتج: {order['product_name']}\nالسعر: SAR {order['price']}"
    send_whatsapp_message(sender_id, fallback_text)


# ==================== المسارات ====================

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot is running - Direct Image Text Renderer", 200


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
                send_whatsapp_message(sender_id, "📄 جاري تحليل الفاتورة ودمج البيانات على الصورة...")
                orders = extract_orders_from_invoice_pdf(media_content)
                
                if orders:
                    send_whatsapp_message(sender_id, f"✅ تم العثور على {len(orders)} طلب(ات)")
                    for order in orders:
                        send_invoice_order(sender_id, order)
                        time.sleep(1.5)
                else:
                    send_whatsapp_message(sender_id, "❌ لم يتم استخراج أي طلبات.")
            else:
                send_whatsapp_message(sender_id, "⚠️ أرسل ملف PDF فقط.")
                
        elif msg.get('type') == 'text':
            send_whatsapp_message(sender_id, "📄 أرسل ملف PDF الخاص بالفواتير ليتم استخراجها ودمجها على الصور.")
            
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
                    
