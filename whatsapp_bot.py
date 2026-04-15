import os
import requests
from flask import Flask, request, jsonify
from process_orders import process_excel_orders
import tempfile

app = Flask(__name__)

# إعدادات WhatsApp Cloud API من بيئة التشغيل
ACCESS_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN')

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    response = requests.post(url, headers=headers, json=data)
    return response.json()

def download_whatsapp_media(media_id):
    url = f"https://graph.facebook.com/v18.0/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res = requests.get(url, headers=headers)
    media_url = res.json().get('url')
    
    if media_url:
        media_res = requests.get(media_url, headers=headers)
        return media_res.content
    return None

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            return challenge, 200
        return 'Forbidden', 403

    data = request.json
    if data.get('object') == 'whatsapp_business_account':
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                messages = value.get('messages', [])
                for msg in messages:
                    sender_id = msg.get('from')
                    
                    # التحقق إذا كانت الرسالة مستند (ملف Excel)
                    if msg.get('type') == 'document':
                        doc = msg.get('document')
                        mime_type = doc.get('mime_type')
                        filename = doc.get('filename', 'file.xlsx')
                        
                        if 'spreadsheet' in mime_type or filename.endswith(('.xlsx', '.xls')):
                            send_whatsapp_message(sender_id, "جاري استلام ملف Excel ومعالجته... يرجى الانتظار ⏳")
                            
                            media_content = download_whatsapp_media(doc.get('id'))
                            if media_content:
                                with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_in:
                                    tmp_in.write(media_content)
                                    input_path = tmp_in.name
                                
                                output_path = input_path.replace('.xlsx', '_processed.txt')
                                
                                # استدعاء وظيفة المعالجة من ملفك process_orders.py
                                success = process_excel_orders(input_path, output_path)
                                
                                if success and os.path.exists(output_path):
                                    with open(output_path, 'r', encoding='utf-8') as f:
                                        result_text = f.read()
                                    
                                    # إرسال النتيجة (نصية أو كملف)
                                    # هنا سنرسلها كنص إذا لم تكن طويلة جداً، أو يمكنك تعديلها لإرسال ملف
                                    if len(result_text) < 4000:
                                        send_whatsapp_message(sender_id, f"✅ تم معالجة الطلبات بنجاح:\n\n{result_text}")
                                    else:
                                        send_whatsapp_message(sender_id, "✅ تم المعالجة، والنتائج جاهزة (النص طويل جداً للواتساب، سأرسل لك ملخصاً قريباً)")
                                else:
                                    send_whatsapp_message(sender_id, "❌ عذراً، حدث خطأ أثناء معالجة الملف. تأكد من تنسيق الأعمدة.")
                                
                                # تنظيف الملفات المؤقتة
                                if os.path.exists(input_path): os.remove(input_path)
                                if os.path.exists(output_path): os.remove(output_path)
                        else:
                            send_whatsapp_message(sender_id, "يرجى إرسال ملف Excel فقط (.xlsx) لمعالجته.")
                    
                    elif msg.get('type') == 'text':
                        text = msg.get('text', {}).get('body', '').lower()
                        send_whatsapp_message(sender_id, "أهلاً بك! أنا بوت معالجة الطلبات. أرسل لي ملف Excel يحتوي على طلباتك وسأقوم بتنظيمها لك فوراً.")

    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
                                
