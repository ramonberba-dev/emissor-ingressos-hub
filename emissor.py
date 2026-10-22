"""Emissor de ingressos do 1º Encontro do Hub de Empreendedores Católicos.

Roda no GitHub Actions a cada 10 minutos:
1. Busca na Nuvemshop os pedidos PAGOS que contêm o ingresso.
2. Para cada pedido novo, gera um código assinado por ingresso (HMAC) e o QR Code.
3. Envia o e-mail com os ingressos ao comprador (Gmail, senha de app).
4. Avisa o Ramon no WhatsApp (CallMeBot) com o link pronto para mandar o ingresso ao comprador.
5. Guarda os pedidos já processados em state.json (só números de pedido, nenhum dado pessoal).

Os logs ficam públicos no GitHub: nunca imprimir nome, e-mail ou telefone de comprador.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import smtplib
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import qrcode
import requests

STORE_ID = "6185693"
PRODUCT_ID = 374834069
PORTARIA_URL = "https://claude.ai/artifact/3cWR4VGqfT63zWuGrsrjU6"
TICKET_PAGE = "https://hubdeempreendedores.usevox.com.br/ingresso/"
FORM_URL = "https://hubdeempreendedores.usevox.com.br/formulario/?pedido="   # formulário dos participantes
REMETENTE = "ramonberba@gmail.com"
# E-mail transacional (Brevo) com remetente do domínio: entra na caixa de entrada com mais frequência.
# Se os segredos do Brevo não estiverem cadastrados, o envio continua pelo Gmail.
BREVO_LOGIN = os.environ.get("BREVO_SMTP_LOGIN", "")
BREVO_KEY = os.environ.get("BREVO_SMTP_KEY", "")
REMETENTE_DOMINIO = os.environ.get("REMETENTE_DOMINIO", "ingressos@usevox.com.br")
INICIO_VENDAS = "2026-10-16T00:00:00-03:00"
FIM = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)  # depois do evento, para de rodar
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
BRT = timezone(timedelta(hours=-3))

SECRET = os.environ.get("TICKET_SECRET", "")
TOKEN = os.environ.get("NUVEMSHOP_TOKEN", "")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
WA_FONE = os.environ.get("WHATSAPP_FONE", "")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
WA_KEY = os.environ.get("CALLMEBOT_APIKEY", "")
TESTE = os.environ.get("TESTE", "").lower() in ("1", "true", "sim")


# ---------- códigos ----------

def primeiro_nome(nome):
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    partes = re.findall(r"[A-Za-z]+", s)
    return (partes[0].upper()[:14]) if partes else "CONVIDADO"


def assinar(msg):
    dig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b32encode(dig).decode().rstrip("=")[:10]


def codigo(pedido, i, n, nome):
    base = f"{pedido}-{i}-{n}-{primeiro_nome(nome)}"
    return f"{base}-{assinar(base)}"


def qr_png(texto):
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(texto)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#2B1E23", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------- Nuvemshop ----------

def api(path, params=None):
    r = requests.get(
        f"https://api.nuvemshop.com.br/v1/{STORE_ID}/{path}",
        headers={"Authentication": f"bearer {TOKEN}", "User-Agent": f"Emissor Hub ({REMETENTE})"},
        params=params or {}, timeout=30,
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()


def pedidos(status):
    out, page = [], 1
    while True:
        lote = api("orders", {"payment_status": status, "created_at_min": INICIO_VENDAS,
                              "per_page": 200, "page": page})
        if not lote:
            break
        out += lote
        if len(lote) < 200:
            break
        page += 1
    return out


def qtd_ingressos(order):
    return sum(int(p.get("quantity") or 0) for p in order.get("products", [])
               if int(p.get("product_id") or 0) == PRODUCT_ID)


def estoque_restante():
    try:
        prod = api(f"products/{PRODUCT_ID}")
        return sum(int(v.get("stock") or 0) for v in prod.get("variants", []) if v.get("stock") is not None)
    except Exception:
        return None


# ---------- e-mail ----------

def email_html(nome, pedido, ingressos):
    blocos = ""
    for k, (cod, cid) in enumerate(ingressos, 1):
        link = TICKET_PAGE + "#" + cod
        blocos += f"""
        <tr><td style="padding:0 28px 22px">
          <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #DCCFC6;border-radius:14px;background:#FFFFFF">
            <tr><td style="padding:20px;text-align:center">
              <div style="font:500 11px Arial,sans-serif;letter-spacing:2px;color:#7A5F36;text-transform:uppercase">Ingresso {k} de {len(ingressos)}</div>
              <img src="cid:{cid}" width="220" height="220" alt="QR Code do ingresso" style="display:block;margin:14px auto;border:0">
              <div style="font:600 13px 'Courier New',monospace;color:#2B1E23;letter-spacing:1px">{cod}</div>
              <a href="{link}" style="display:inline-block;margin-top:14px;background:#5A2439;color:#F5EEE6;text-decoration:none;font:600 14px Arial,sans-serif;padding:12px 22px;border-radius:999px">Abrir ingresso no celular</a>
            </td></tr>
          </table>
        </td></tr>"""
    return f"""<!doctype html><html><body style="margin:0;background:#F6F2EF">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F6F2EF;padding:24px 0"><tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#F6F2EF">
  <tr><td style="background:#4A1C2F;border-radius:16px 16px 0 0;padding:26px 28px">
    <div style="font:300 14px Arial,sans-serif;letter-spacing:6px;color:#F5EEE6">HUB</div>
    <div style="font:italic 15px Georgia,serif;color:#D9C7B9">de Empreendedores Católicos</div>
    <div style="font:600 30px Georgia,serif;color:#F5EEE6;margin-top:18px;line-height:1.15">Seu ingresso está garantido, {nome}!</div>
  </td></tr>
  <tr><td style="background:#EFE7E1;padding:20px 28px;font:15px/1.6 Arial,sans-serif;color:#2B1E23">
    <b>1º Encontro do Hub de Empreendedores Católicos</b><br>
    Sábado, 14 de novembro de 2026 · 10h às 13h30<br>
    Coco Bambu Niterói · Plaza Shopping, 4º piso · Rua XV de Novembro, 8 · Centro · Niterói/RJ<br>
    <span style="color:#6E5D63">Pedido #{pedido}</span>
  </td></tr>
  <tr><td style="padding:22px 28px 14px;font:15px/1.6 Arial,sans-serif;color:#2B1E23">
    Apresente o QR Code abaixo na entrada, no celular ou impresso. Cada QR Code vale para <b>uma pessoa</b> e só pode ser usado uma vez.
  </td></tr>
  {blocos}
  <tr><td style="padding:0 28px 22px">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#4A1C2F;border-radius:14px">
      <tr><td style="padding:22px;text-align:center;font:15px/1.55 Arial,sans-serif;color:#F5EEE6">
        <div style="font:600 20px Georgia,serif;color:#F5EEE6">Falta só um passo</div>
        <div style="margin-top:8px;color:#D9C7B9">Para personalizarmos a sua experiência (a mesa, as conexões e os exemplos das palestras), responda algumas perguntas rápidas. Leva uns 2 minutos.</div>
        <a href="{FORM_URL}{pedido}" style="display:inline-block;margin-top:16px;background:#B89F77;color:#2A1018;text-decoration:none;font:600 14px Arial,sans-serif;padding:12px 24px;border-radius:999px">Responder agora</a>
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:4px 28px 26px;font:14px/1.6 Arial,sans-serif;color:#6E5D63">
    Credenciamento a partir das 10h. Tem restrição alimentar ou alguma dúvida? Fale com a gente no WhatsApp (21) 99658-8197.<br><br>
    <i style="font-family:Georgia,serif;color:#5A2439">"Se o Senhor não edificar a casa, em vão trabalham os que a edificam." (Sl 127,1)</i>
  </td></tr>
</table></td></tr></table></body></html>"""


def enviar_email(destino, nome, pedido, codigos):
    msg = MIMEMultipart("related")
    msg["Subject"] = f"Seu ingresso · 1º Encontro do Hub (14/11) · Pedido #{pedido}"
    usa_brevo = bool(BREVO_LOGIN and BREVO_KEY)
    de = REMETENTE_DOMINIO if usa_brevo else REMETENTE
    msg["From"] = formataddr(("Hub de Empreendedores Católicos", de))
    msg["To"] = destino
    msg["Reply-To"] = REMETENTE
    ingressos = [(c, f"qr{k}") for k, c in enumerate(codigos, 1)]
    alt = MIMEMultipart("alternative")
    texto = (f"Olá, {nome}! Seu ingresso para o 1º Encontro do Hub (sábado, 14/11, 10h, Coco Bambu Niterói) "
             f"está garantido. Pedido #{pedido}.\n\n" +
             "\n".join(f"Ingresso {k}: {TICKET_PAGE}#{c}" for k, (c, _) in enumerate(ingressos, 1)) +
             f"\n\nPara personalizarmos a sua experiência, responda o formulário rápido (2 minutos): {FORM_URL}{pedido}"
             "\n\nApresente o QR Code na entrada. Dúvidas: WhatsApp (21) 99658-8197.")
    alt.attach(MIMEText(texto, "plain", "utf-8"))
    alt.attach(MIMEText(email_html(nome, pedido, ingressos), "html", "utf-8"))
    msg.attach(alt)
    for cod, cid in ingressos:
        img = MIMEImage(qr_png(PORTARIA_URL + "#" + cod), "png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"ingresso-{cod[:12]}.png")
        msg.attach(img)
    if usa_brevo:
        with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=60) as s:
            s.starttls()
            s.login(BREVO_LOGIN, BREVO_KEY)
            s.sendmail(de, [destino], msg.as_string())
        return
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(REMETENTE, GMAIL_PASS)
        s.sendmail(REMETENTE, [destino], msg.as_string())


# ---------- WhatsApp para o Ramon ----------

def so_digitos(fone):
    d = re.sub(r"\D", "", fone or "")
    if len(d) in (10, 11):
        d = "55" + d
    return d


def avisar_telegram(texto):
    if not (TG_TOKEN and TG_CHAT):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          data={"chat_id": TG_CHAT, "text": texto, "disable_web_page_preview": "true"}, timeout=30)
        print("Aviso no Telegram:", r.status_code)
        return r.ok
    except Exception as e:
        print("Aviso no Telegram falhou:", type(e).__name__)
        return False


def avisar_ramon(texto):
    """texto: str ou lista de partes. No WhatsApp (CallMeBot corta ~720 caracteres) cada parte vai
    numa mensagem separada; no Telegram vai tudo junto."""
    partes = [texto] if isinstance(texto, str) else list(texto)
    avisar_telegram("\n\n".join(partes))
    if not (WA_FONE and WA_KEY):
        print("Aviso no WhatsApp ignorado: segredos do CallMeBot não configurados.")
        return
    envios = []
    for p in partes:
        while len(p) > 550:
            corte = p.rfind(" ", 0, 550)
            corte = corte if corte > 300 else 550
            envios.append(p[:corte]); p = p[corte:].lstrip()
        envios.append(p)
    for k, p in enumerate(envios):
        if k:
            time.sleep(8)
        try:
            r = requests.get("https://api.callmebot.com/whatsapp.php",
                             params={"phone": WA_FONE, "text": p, "apikey": WA_KEY}, timeout=30)
            print(f"Aviso no WhatsApp {k + 1}/{len(envios)}:", r.status_code)
        except Exception as e:
            print("Aviso no WhatsApp falhou:", type(e).__name__)


def aviso_venda(nome, fone, pedido, n, gateway, codigos, restam):
    primeiro = (nome or "").split(" ")[0] or "tudo bem"
    links = " ".join(f"{TICKET_PAGE}#{c}" for c in codigos)
    msg_cliente = (f"Olá, {primeiro}! Aqui é o Ramon, do Hub de Empreendedores Católicos. "
                   f"Seu ingresso para o 1º Encontro (sábado, 14/11, 10h, Coco Bambu Niterói): {links} "
                   f"Para personalizarmos a sua experiência, responda estas perguntas rápidas (2 minutos): {FORM_URL}{pedido} "
                   f"Apresente o QR Code na entrada. Até lá! 🙏")
    d = so_digitos(fone)
    conversa = f"https://wa.me/{d}" if d else "(comprador sem telefone)"
    estoque = f"\n🔢 Restam {restam} no lote atual" if restam is not None else ""
    info = (f"🎟️ Ingresso vendido! 1º Encontro do Hub\n👤 {nome} · {n} ingresso(s) · {gateway}\n"
            f"📱 {fone or '-'}\n🧾 Pedido #{pedido}{estoque}\n✉️ E-mail com o QR já enviado.\n"
            f"👉 Abrir conversa: {conversa}\n📋 Na próxima mensagem vai o texto para copiar e colar.")
    return [info, msg_cliente]


# ---------- estado ----------

def carregar_estado():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        st = {}
    st.setdefault("processados", [])
    st.setdefault("cancelamentos_avisados", [])
    return st


def salvar_estado(st):
    st["atualizado_em"] = datetime.now(BRT).isoformat(timespec="seconds")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    faltando = [k for k, v in {"TICKET_SECRET": SECRET, "GMAIL_APP_PASSWORD ou BREVO": GMAIL_PASS or (BREVO_LOGIN and BREVO_KEY)}.items() if not v]
    if not TESTE and not TOKEN:
        faltando.append("NUVEMSHOP_TOKEN")
    if faltando:
        print("Faltam segredos:", ", ".join(faltando))
        sys.exit(1)

    if TESTE:
        nome = "Ramon Berba"
        cods = [codigo(0, 1, 1, nome)]
        enviar_email(REMETENTE, nome, 0, cods)
        teste = aviso_venda(nome, "21996588197", 0, 1, "Pix", cods, None)
        avisar_ramon(["[TESTE] " + teste[0]] + teste[1:])
        print("Teste enviado para o e-mail do Ramon (pedido 0).")
        return

    flag = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aviso_teste.flag")
    if os.path.exists(flag):
        teste = aviso_venda("Teste do aviso", "21996588197", 0, 1, "Pix", [codigo(0, 1, 1, "Teste")], None)
        avisar_ramon(["[TESTE] " + teste[0]] + teste[1:])
        os.remove(flag)
        print("Aviso de teste enviado.")

    if datetime.now(timezone.utc) > FIM:
        print("Evento encerrado: nada a fazer.")
        return

    st = carregar_estado()
    feitos = set(map(str, st["processados"]))
    novos = 0
    restam = None
    for o in sorted(pedidos("paid"), key=lambda o: o.get("number", 0)):
        n = qtd_ingressos(o)
        if n == 0 or str(o["id"]) in feitos:
            continue
        cust = o.get("customer") or {}
        nome = (o.get("contact_name") or cust.get("name") or "").strip() or "Participante"
        email = (o.get("contact_email") or cust.get("email") or "").strip()
        fone = (o.get("contact_phone") or cust.get("phone") or "").strip()
        pedido = o.get("number")
        cods = [codigo(pedido, i, n, nome) for i in range(1, n + 1)]
        if not email:
            avisar_ramon(f"⚠️ Pedido #{pedido} pago sem e-mail. Emita o ingresso manualmente.")
            continue
        try:
            enviar_email(email, nome.split(" ")[0], pedido, cods)
        except Exception as e:
            print(f"Pedido #{pedido}: falha no e-mail ({type(e).__name__}); tento de novo na próxima rodada.")
            continue
        if restam is None:
            restam = estoque_restante()
        gateway = o.get("payment_details", {}).get("method") or o.get("gateway_name") or o.get("gateway") or "-"
        avisar_ramon(aviso_venda(nome, fone, pedido, n, gateway, cods, restam))
        st["processados"].append(str(o["id"]))
        feitos.add(str(o["id"]))
        salvar_estado(st)
        novos += 1
        print(f"Pedido #{pedido}: {n} ingresso(s) emitido(s).")

    avisados = set(map(str, st["cancelamentos_avisados"]))
    for status in ("refunded", "voided", "chargeback"):
        for o in pedidos(status):
            if str(o["id"]) in feitos and str(o["id"]) not in avisados and qtd_ingressos(o):
                avisar_ramon(f"⚠️ Pedido #{o.get('number')} foi {status}. Peça ao Claude para bloquear "
                             f"esse ingresso na Portaria.")
                st["cancelamentos_avisados"].append(str(o["id"]))
                avisados.add(str(o["id"]))
                salvar_estado(st)

    print(f"Rodada concluída: {novos} pedido(s) novo(s).")


if __name__ == "__main__":
    main()
