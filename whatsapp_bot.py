import os
import requests
import re
import tempfile
import time
import pandas as pd
from flask import Flask, request, jsonify

app = Flask(__name__)

# المتغيرات الخاصة بك من بيئة Render
SALLA_TOKEN = os.getenv("SALLA_TOKEN")
META_TOKEN = os.getenv("META_TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "aliza_secure_pass")

# ذاكرة مؤقتة لمنع تكرار معالجة نفس الرسالة في نفس الوقت
processed_messages = set()

# الذاكرة المؤقتة لملف الإكسل والعدادات
excel_memory = {}
stats = {"total": 0, "processed": 0}

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {META_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        print(f"WhatsApp message sent successfully to {to}.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error sending WhatsApp message to {to}: {e}")
        return False

@app.route('/webhook', methods=['GET'])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Failed", 403

@app.route('/webhook', methods=['POST'])
def webhook():
    global excel_memory, stats
    data = request.json
    
    try:
        # استخراج معلومات الرسالة الأساسية
        message_data = data['entry'][0]['changes'][0]['value']['messages'][0]
        msg_id = message_data.get('id')
        sender = message_data.get('from')
        msg_type = message_data.get('type')

        # منع التكرار
        if msg_id in processed_messages:
            return jsonify({"status": "duplicate"}), 200
        processed_messages.add(msg_id)
        if len(processed_messages) > 1000: processed_messages.clear()

        # -----------------------------------------------------------
        # المسار الأول: استقبال ملف الإكسل وحفظه بالذاكرة
        # -----------------------------------------------------------
        if msg_type == 'document':
            doc = message_data['document']
            filename = doc.get('filename', '').lower()
            mime_type = doc.get('mime_type', '')
            media_id = doc.get('id')
            
            if 'spreadsheet' in mime_type or filename.endswith(('.xlsx', '.xls')):
                send_whatsapp_message(sender, "📥 جاري قراءة ملف الإكسل وحفظ البيانات للبحث السريع... ⏳")
                
                # جلب رابط تحميل الملف من ميتا
                headers = {"Authorization": f"Bearer {META_TOKEN}"}
                res = requests.get(f"https://graph.facebook.com/v17.0/{media_id}", headers=headers).json()
                media_url = res.get('url')
                
                if media_url:
                    media_content = requests.get(media_url, headers=headers).content
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                        tmp.write(media_content)
                        path = tmp.name
                    
                    try:
                        df = pd.read_excel(path)
                        
                        # تصفير وإعادة شحن الذاكرة
                        excel_memory = {}
                        stats["total"] = len(df)
                        stats["processed"] = 0
                        
                        for _, row in df.iterrows():
                            # تنظيف رقم الطلب وتحويله لنص
                            order_id = str(row.get('رقم الطلب', '')).split('.')[0].strip()
                            excel_memory[order_id] = {
                                "address": row.get('العنوان', 'غير متوفر'),
                                "phone": row.get('رقم المستلم', 'غير متوفر'),
                                "name": row.get('اسم المستلم', 'غير متوفر'),
                                "status": "pending"
                            }
                        
                        initial_report = (
                            f"📊 *تقرير الملف المرفوع:*\n\n"
                            f"🔹 إجمالي الطلبات في الملف: {stats['total']}\n"
                            f"⏳ طلبات بانتظار الاستخراج: {stats['total']}\n"
                            f"✅ تم تنفيذ: 0\n\n"
                            f"جاهز الآن! أرسل أرقام الطلبات (كل رقم في سطر) لطباعة العناوين فوراً."
                        )
                        send_whatsapp_message(sender, initial_report)
                        
                    except Exception as e:
                        print(f"Excel Error: {e}")
                        send_whatsapp_message(sender, "❌ حدث خطأ أثناء تحليل بيانات ملف الإكسل. تأكد من أسماء الأعمدة.")
                    finally:
                        if os.path.exists(path): os.remove(path)
            else:
                send_whatsapp_message(sender, "⚠️ يرجى إرسال ملف بصيغة Excel فقط.")

        # -----------------------------------------------------------
        # المسار الثاني: استقبال أرقام الطلبات نصياً والبحث عنها
        # -----------------------------------------------------------
        elif msg_type == 'text':
            msg_body = message_data['text']['body'].strip()
            
            # استخراج كافة الأرقام المكونة من 5 خانات فأكثر من الرسالة مجتمعة
            order_ids = re.findall(r'\b\d{5,}\b', msg_body)
            
            if order_ids and excel_memory:
                responses = []
                found_count = 0
                
                for oid in order_ids:
                    if oid in excel_memory:
                        # إذا لم يتم معالجته مسبقاً، يضاف للعداد ويتحول المتبقي
                        if excel_memory[oid]["status"] == "pending":
                            stats["processed"] += 1
                            excel_memory[oid]["status"] = "completed"
                        
                        data = excel_memory[oid]
                        found_count += 1
                        formatted_res = (
                            f"العنوان/ {data['address']}\n"
                            f"رقم الطلبية/ {oid}\n"
                            f"رقم المستلم/ {data['phone']}\n"
                            f"اسم المستلم/ {data['name']}"
                        )
                        responses.append(formatted_res)
                    else:
                        responses.append(f"❌ الطلب {oid}: غير موجود في الملف المرفوع.")
                
                # حساب الإحصائيات المتبقية
                remaining = stats["total"] - stats["processed"]
                summary_report = (
                    f"📝 *تم استخراج {found_count} عناوين بنجاح*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📊 *الحالة العامة للملف:*\n"
                    f"✅ إجمالي المنفذ حتى الآن: {stats['processed']}\n"
                    f"⏳ المتبقي عليك: {remaining}\n"
                    f"🔢 إجمالي طلبات الملف: {stats['total']}"
                )
                
                # إرسال قائمة العناوين كاملة في رسالة واحدة تفصل بينها خطوط
                full_addresses = "\n\n----------------\n\n".join(responses)
                send_whatsapp_message(sender, full_addresses)
                
                # إرسال تقرير العدادات بعد ثانية
                time.sleep(1)
                send_whatsapp_message(sender, summary_report)
                
            elif order_ids and not excel_memory:
                send_whatsapp_message(sender, "⚠️ الذاكرة فارغة حالياً. يرجى إرسال ملف الإكسل أولاً ليتمكن البوت من قراءته والبحث فيه.")
            else:
                send_whatsapp_message(sender, "أهلاً بك يا أنس! أرسل ملف Excel لفرز وجدولة الطلبات، أو أرسل أرقام الطلبات مباشرة لاستخراج العناوين.")

    except Exception as e:
        print(f"General Webhook Error: {e}")
        
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
        
