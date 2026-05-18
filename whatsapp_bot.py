Import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# هذه المتغيرات سنضبطها في Render للأمان
SALLA_TOKEN = os.getenv("SALLA_TOKEN")
META_TOKEN = os.getenv("META_TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "aliza_secure_pass")

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {META_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    requests.post(url, json=payload, headers=headers)

@app.route('/webhook', methods=['GET'])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Failed", 403

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    try:
        msg_body = data['entry'][0]['changes'][0]['value']['messages'][0]['text']['body']
        sender = data['entry'][0]['changes'][0]['value']['messages'][0]['from']
        
        # استخراج رقم الطلب (بافتراض أنه رقم مكون من عدة خانات)
        import re
        order_id = re.findall(r'\d+', msg_body)
        
        if order_id:
            id_val = order_id[0]
            if "عنوان" in msg_body:
                # هنا سنضيف كود جلب العنوان من سلة لاحقاً
                send_whatsapp_message(sender, f"جاري جلب عنوان الطلب {id_val} من متجر أليزا...")
            elif "تم التنفيذ" in msg_body:
                # هنا سنضيف كود تغيير الحالة في سلة لاحقاً
                send_whatsapp_message(sender, f"سيتم تغيير حالة الطلب {id_val} إلى تم التنفيذ.")
        else:
            send_whatsapp_message(sender, "أهلاً أنس، يرجى كتابة رقم الطلب مع الأمر (مثلاً: عنوان الطلب 123)")
            
    except: pass
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
